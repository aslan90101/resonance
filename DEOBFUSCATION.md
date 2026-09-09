# Resonanse.lua deobfuscation

`Resonanse.lua` is a Luraph-protected Luau script. The visible file is only a
container: it base-85-decodes two streams and uses an embedded range decoder to
load a generated VM.

The reproducible extraction command is:

```sh
python3 deobfuscate_resonanse.py Resonanse.lua \
  --output Resonanse_deobfuscated.lua \
  --payload-output /tmp/Resonanse_serialized_payload.bin
```

The resulting `Resonanse_deobfuscated.lua` is the fully decompressed generated
Luraph VM source (329,943 bytes). The payload file is the corresponding
serialized VM program (2,260,759 bytes); it is intentionally written to `/tmp`
in the command above because it is binary intermediate data, not Lua source.

## Current recovery status

`Resonanse_recovered.lua` is an additional register-level listing of the
serialized VM program. It contains all 355 recovered prototype frames and
32,512 decoded instruction rows, preserves the raw operands, resolves nested
prototype references, and corrects the VM's one-based jump targets. It is useful
for auditing and continuing the devirtualization, but it is **not** the original
author-written Lua source and is not intended to be executed as Lua.

The remaining step is source-level devirtualization: prove the dispatcher
semantics for every reachable virtual opcode, reconstruct the reachable control
flow and expressions, and recover names/closures from the VM register state.
Until that pass is complete, the original Lua source has not been recovered.

This distinction is deliberate: the decompressed VM scaffold and the register
listing must not be presented as the original script.
