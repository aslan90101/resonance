#!/usr/bin/env python3
"""Decode the Luraph container used by ``Resonanse.lua``.

The input is a Luau/Luraph wrapper, not a plain Lua source file.  It contains
 two base-85 encoded streams.  Each stream is decoded and then expanded by the
range coder used by the wrapper.  The first stream is the generated Luraph VM
(loader source); the second stream is the VM's serialized program data.

This intentionally stops at the VM boundary.  Luraph virtualizes the original
program, so the VM source is not the original author-written Lua source.  The
script nevertheless makes the binary decoding reproducible and writes both
useful intermediate artifacts.
"""

from __future__ import annotations

import argparse
import re
import struct
from pathlib import Path


HEADER_INDEX = 5  # string.sub(stream, 5) is one-based; four chars are removed


def _find_streams(source: str) -> tuple[str, str]:
    """Return the [=[...]=] and [==[...]==] payloads in the wrapper."""
    first_open = source.index("[=[") + 3
    first_close = source.index("]=]", first_open)
    second_open = source.index("[==[", first_close) + 4
    second_close = source.index("]==]", second_open)
    return source[first_open:first_close], source[second_open:second_close]


def decode_base85(stream: str) -> bytes:
    """Implement the wrapper's five-character/4-byte decoder.

    ``z`` is expanded to the five-character zero group before decoding.  The
    wrapper calls string.sub with index 5, hence the slice at index 4 here.
    """
    stream = stream[HEADER_INDEX - 1 :].replace("z", "!!!!!")
    if len(stream) % 5:
        raise ValueError(f"invalid encoded stream length: {len(stream)}")

    decoded = bytearray()
    for offset in range(0, len(stream), 5):
        q, j, g, s, t = (ord(c) for c in stream[offset : offset + 5])
        value = (
            (t - 33)
            + (s - 33) * 85
            + (g - 33) * 7225
            + (j - 33) * 614125
            + (q - 33) * 52200625
        )
        decoded.extend(struct.pack("<I", value & 0xFFFFFFFF))
    return bytes(decoded)


class RangeDecoder:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.range = 0xFFFFFFFF
        self.code = 0
        for _ in range(5):
            self.code = (self.code << 8) | self._read()

    def _read(self) -> int:
        if self.pos >= len(self.data):
            raise EOFError(f"range stream ended at {self.pos}/{len(self.data)}")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def _normalize(self) -> None:
        if self.range <= 0x00FFFFFF:
            self.range <<= 8
            self.code = (self.code << 8) | self._read()

    def bit(self, probabilities: list[int], index: int) -> int:
        probability = probabilities[index]
        scale = self.range // 2048
        bound = scale * probability
        if self.code < bound:
            self.range = bound
            probabilities[index] = probability + (2048 - probability) // 32
            result = 0
        else:
            self.range -= bound
            self.code -= bound
            probabilities[index] = probability - probability // 32
            result = 1
        self._normalize()
        return result

    def tree(self, probabilities: list[int], bits: int, offset: int) -> int:
        node = 1
        for _ in range(bits):
            node = node * 2 + self.bit(probabilities, node)
        return node - offset

    def direct_bits(self, bits: int) -> int:
        value = 0
        for _ in range(bits, 0, -1):
            self.range //= 2
            value *= 2
            if not (self.code < self.range):
                self.code -= self.range
                value += 1
            self._normalize()
        return value

    def reverse_tree(
        self, probabilities: list[int], bits: int, offset: int
    ) -> int:
        value = 0
        node = 1
        for bit_index in range(bits):
            bit = self.bit(probabilities, offset + node)
            node = node * 2 + bit
            value += bit * (1 << bit_index)
        return value


class MatchLengthProbabilities:
    def __init__(self) -> None:
        self.choice = [1024, 1024]  # Lua indexes 1 and 2
        self.low = [[1024] * 8]
        self.middle = [[1024] * 8]
        self.high = [1024] * 256

    def bit(self, decoder: RangeDecoder, lua_index: int) -> int:
        return decoder.bit(self.choice, lua_index - 1)


def range_decompress(data: bytes) -> bytes:
    """Translate the small LZMA-like decoder embedded in the wrapper."""
    decoder = RangeDecoder(data)
    powers = [1 << bit for bit in range(32)]

    def vector(size: int) -> list[int]:
        return [1024] * size

    def matrix(columns: int, rows: int) -> list[list[int]]:
        return [[1024] * columns for _ in range(rows)]

    # Names and dimensions mirror the locals in the generated Lua decoder.
    literal_probabilities = matrix(0x300, 8)
    previous_1 = previous_2 = 0
    match_state = matrix(1, 12)
    is_match = vector(12)
    is_rep = vector(12)
    is_rep_g0 = vector(12)
    rep_0 = 0
    is_rep_g1 = vector(12)
    is_rep0_long = matrix(1, 12)
    distance_slot = matrix(64, 4)
    align = vector(115)
    distance_special = vector(16)
    match_length = MatchLengthProbabilities()
    rep_match_length = MatchLengthProbabilities()
    rep_3 = 0

    state = 0
    output = [0]
    position = 0
    state_transition = [0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 4, 5]

    def decode_match_length(context: int, probabilities: MatchLengthProbabilities) -> int:
        if probabilities.bit(decoder, 1) == 0:
            return decoder.tree(probabilities.low[context], 3, 8)
        if probabilities.bit(decoder, 2) == 0:
            return 8 + decoder.tree(probabilities.middle[context], 3, 8)
        return decoder.tree(probabilities.high, 8, 256) + 16

    def decode_literal(context: int, probabilities: list[int]) -> int:
        node = 1
        for bit_position in range(7, -1, -1):
            context_bit = (context // powers[bit_position]) % 2
            decoded = decoder.bit(
                probabilities, node + context_bit * 256 + 256
            )
            node = node * 2 + decoded
            if context_bit != decoded:
                while node < 0x100:
                    node = node * 2 + decoder.bit(probabilities, node)
                break
        return node % 256

    while decoder.pos <= len(data):
        position_state = position % 1
        if decoder.bit(match_state[state], position_state) == 0:
            previous = output[position]
            literal_context = previous // powers[5]
            probabilities = literal_probabilities[literal_context]
            position += 1
            if state < 7:
                value = decoder.tree(probabilities, 8, 256)
            else:
                value = decode_literal(output[position - previous_2 - 1], probabilities)
            output.append(value)
            state = state_transition[state]
            continue

        length = None
        if decoder.bit(is_match, state) != 0:
            if decoder.bit(is_rep, state) == 0:
                if decoder.bit(is_rep0_long[state], position_state) == 0:
                    state = 9 if state < 7 else 11
                    length = 1
            else:
                if decoder.bit(is_rep_g0, state) == 0:
                    distance = previous_1
                else:
                    if decoder.bit(is_rep_g1, state) == 0:
                        distance = rep_3
                    else:
                        distance = rep_0
                        rep_0 = rep_3
                    rep_3 = previous_1
                previous_1 = previous_2
                previous_2 = distance
            if length is None:
                state = 8 if state < 7 else 11
                length = 2 + decode_match_length(position_state, rep_match_length)
        else:
            rep_0, rep_3, previous_1 = rep_3, previous_1, previous_2
            length = 2 + decode_match_length(position_state, match_length)
            slot_context = length - 2
            if slot_context >= 4:
                slot_context = 3
            distance_value = decoder.tree(distance_slot[slot_context], 6, 64)
            if distance_value >= 4:
                slot = distance_value
                direct_count = slot // 2 - 1
                distance_value = (2 + slot % 2) * powers[direct_count]
                if slot < 14:
                    distance_value += decoder.reverse_tree(
                        align, direct_count, distance_value - slot
                    )
                else:
                    distance_value += decoder.direct_bits(direct_count - 4) * 16
                    distance_value += decoder.reverse_tree(distance_special, 4, 0)
                    if distance_value == 0xFFFFFFFF:
                        return bytes(output[1:])
            previous_2 = distance_value
            state = 7 if state < 7 else 10
            if previous_2 >= position:
                return bytes(output[1:])

        end = position + length
        for index in range(position + 1, end + 1):
            output.append(output[index - previous_2 - 1])
        position = end

    return bytes(output[1:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="Resonanse.lua")
    parser.add_argument(
        "--output", default="Resonanse_deobfuscated.lua", help="decoded VM source"
    )
    parser.add_argument(
        "--payload-output",
        default="Resonanse_serialized_payload.bin",
        help="serialized VM program data",
    )
    args = parser.parse_args()

    source = Path(args.input).read_text(encoding="utf-8")
    encoded_vm, encoded_payload = _find_streams(source)
    vm = range_decompress(decode_base85(encoded_vm))
    payload = range_decompress(decode_base85(encoded_payload))

    Path(args.output).write_bytes(vm)
    Path(args.payload_output).write_bytes(payload)
    print(f"VM source: {args.output} ({len(vm)} bytes)")
    print(f"Serialized payload: {args.payload_output} ({len(payload)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
