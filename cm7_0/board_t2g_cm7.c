/*
 * Copyright 2026 Arm Limited and/or its affiliates.
 *
 * This source code is licensed under the BSD-style license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * KIT_T2G-B-H_LITE Cortex-M7 core 0 board support for the all-ops runner.
 *
 * This core is released by the CM0+ image only after the CM0+ all-ops run has
 * finished, so the device is already fully configured (init_cycfg_all ran on
 * the CM0+) and the shared KitProg3 UART is idle. Only two things are needed
 * here: re-arm the SCB0 UART driver context on this core, and unlock the DWT
 * (the Cortex-M7 gates DWT_CTRL writes behind DWT_LAR, unlike the Cortex-M85
 * the FVP build targets) so main()'s cycle counter enable takes effect.
 * I/D caches are enabled by the Infineon startup code.
 */

#include <stdint.h>

extern int retarget_stdio_init(void);

__attribute__((constructor)) static void board_init(void) {
  /* DWT_LAR (not in the CMSIS DWT_Type): unlock so main() can enable CYCCNT */
  *(volatile uint32_t *)0xE0001FB0u = 0xC5ACCE55u;
  retarget_stdio_init();
}
