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

## Important limitation

Luraph virtualizes the author-written program. Therefore the decompressed VM
source is not the original author-written Lua source: the original semantics
are encoded in the serialized VM payload and are interpreted at runtime. A
source-level recovery would require a second devirtualization pass (recovering
the VM opcode map, constants, control flow, and Roblox/Luau host objects). The
repository now contains the complete decoded VM layer and a deterministic
extractor rather than falsely presenting the VM scaffold as the original code.
