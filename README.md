# ExecuTorch all-ops test on Infineon TRAVEO™ T2G (dual-core)

Board port of the ExecuTorch CMSIS-Pack **all-ops test** to the Infineon
[KIT_T2G-B-H_LITE](https://www.infineon.com/cms/en/product/evaluation-boards/kit_t2g-b-h_lite/)
kit (TRAVEO T2G CYT4BF8CDS: 1× Cortex-M0+, 2× Cortex-M7, 8 MB code flash,
1 MB SRAM). Structure and board bring-up follow the Arm-Examples
[Safety-Example-Infineon-T2G](https://github.com/Arm-Examples/Safety-Example-Infineon-T2G)
reference.

Both of the device's instruction-set regimes run the same test, each with
models whose ahead-of-time CMSIS-NN scratch is sized for that core:

| Project | Core | CMSIS-NN path | Models |
|---|---|---|---|
| `cm0plus/all_ops_m0plus` | Cortex-M0+ (Armv6-M) | SCALAR (no DSP/MVE) | 167 `.pte`, SCALAR-sized scratch |
| `cm7_0/all_ops_cm7` | Cortex-M7 core 0 | DSP extension | 167 `.pte`, DSP-sized scratch (`--target-core m7`) |

Every model produced by `generate_test_models.py` is embedded into the ELF
(`embedded_models_blob.S`); each `.pte` carries its own test inputs, expected
outputs and tolerances. The runner executes every op, prints a per-op
PASS/FAIL table with cycle counts, a memory high-water-mark report
(`MemReport:`) and a final `Test_result: SUMMARY <pass>/<total> PASS`.

## How the two tests share one board

The CM0+ is the TRAVEO T2G boot core and owns the single KitProg3 USB-UART:

1. The **CM0+ image** configures the device (Device Configurator output),
   routes stdout to SCB0 (KitProg3 VCP, 115200 baud) and runs the SCALAR
   all-ops test.
2. After printing its summary it releases **CM7 core 0**
   (`board_all_ops_done()` in `cm0plus/board_t2g_cm0p.c` — the same CPUSS
   register sequence the Safety example's boot app uses).
3. The **CM7_0 image** then runs the DSP-path test and prints its own table.

The two runs appear sequentially on the same serial port and never interleave.
CM7 core 1 is not used.

## Memory layout

One partitioning, kept identical in both linker scripts
(`cm0plus/xmc7200d_x8384_cm0plus.ld`, `cm7_0/xmc7200d_x8384_cm7_0.ld`,
derived from the T2G-B-H DFP GCC templates):

| Region | CM0+ | CM7_0 |
|---|---|---|
| Code flash | 3 MB @ `0x10000000` | 5 MB @ `0x10300000` |
| SRAM | 384 KB @ `0x28000000` | 608 KB @ `0x28060000` |

The runtime pools are sized down from the FVP defaults via
`METHOD_POOL_KB=128 / TEMP_POOL_KB=32 / META_POOL_KB=16 / META_TEMP_POOL_KB=8`
(measured peaks are ~11 KB; see the `MemReport:` output) so both images fit
the shared 1 MB SRAM. `CM7_0_APP_VECTOR_TABLE_BASE` in `board_t2g_cm0p.c`
must match the CM7 flash base if you change the partitioning.

Two LTO pitfalls of the stock DFP files are handled in this repo (see comments
in place): the CM7 linker script must not assign `__Vectors` itself, and
`SysLib_FaultHandler` in the RTE `startup_cm7.c` needs
`__attribute__((used))` — otherwise `-flto` drops the flash vector table
and/or the fault handler.

## Prerequisites

- [CMSIS-Toolbox](https://github.com/Open-CMSIS-Pack/cmsis-toolbox) 2.14.1,
  Arm GNU Toolchain (GCC) 14.3.1, CMake ≥ 4, Ninja — all pinned in
  `vcpkg-configuration.json` (`vcpkg activate` sets everything up).
- The **PyTorch::ExecuTorch pack is bundled** under `packs/`:

  ```sh
  cpackget add ./packs/PyTorch.ExecuTorch.1.4.0-stage.pack --agree-embedded-license
  ```

- Public packs (CMSIS 6, CMSIS-NN, CMSIS-Compiler, Infineon T2G-B-H DFP + BSP)
  resolve from the public index:

  ```sh
  csolution list packs all_ops_t2g.csolution.yml -m > packs.txt
  cpackget add -f packs.txt --agree-embedded-license
  ```

## Build

```sh
cbuild all_ops_t2g.csolution.yml --update-rte \
  --context all_ops_m0plus.Release+T2G-Kit \
  --context all_ops_cm7.Release+T2G-Kit
```

Only the `Release` build-type exists: the whole-operator-surface image links
every ExecuTorch operator (~6 MB at `-O0`), which only fits the flash
partitions with `-Os` + LTO. `debug: on` keeps full DWARF, so on-target
debugging works.

## Run

Flash both images (the solution's target-set lists both, with a
`KitProg3@pyOCD` debugger configuration — the CMSIS Solution VS Code
extension's Load/Run buttons use it directly). With pyOCD manually:

```sh
pyocd flash -t cyt4bf_8m out/all_ops_m0plus/T2G-Kit/Release/all_ops_m0plus.hex
pyocd flash -t cyt4bf_8m out/all_ops_cm7/T2G-Kit/Release/all_ops_cm7.hex
```

Then open the KitProg3 VCP at 115200-8-N-1 and reset the board. Expected
output: the CM0+ per-op table and summary first, then the CM7_0 table:

```
==== Per-op results (167 models) ====
op                             result   model(B)    in(B)   out(B)       cycles     max|err|
...
Test_result: SUMMARY 167/167 PASS      <- CM0+ (SCALAR)
...
Test_result: SUMMARY 167/167 PASS      <- CM7_0 (DSP)
```

## Layout

```
all_ops_t2g.csolution.yml   solution: one target (T2G-Kit), two core projects
vcpkg-configuration.json    toolchain pins (cmsis-toolbox 2.14.1, GCC 14.3.1)
common/                     shared runner (main.cpp, PAL override, shims)
cm0plus/                    CM0+ project: SCALAR models, boot/hand-off, linker script
cm7_0/                      CM7_0 project: DSP models, DWT unlock, linker script
board/                      retarget_stdio.c (SCB0 UART) + Device Configurator output
packs/                      bundled PyTorch::ExecuTorch CMSIS pack
```

## Licensing

Project files are BSD-3-Clause (see `LICENSE`). Files derived from Infineon
device support material and from the Safety-Example-Infineon-T2G repository
carry Apache-2.0 headers, as noted in `LICENSE`.
