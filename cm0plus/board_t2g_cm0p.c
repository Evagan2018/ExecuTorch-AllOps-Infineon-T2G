/*
 * Copyright 2026 Arm Limited and/or its affiliates.
 *
 * This source code is licensed under the BSD-style license found in the
 * LICENSE file in the root directory of this source tree.
 *
 * KIT_T2G-B-H_LITE Cortex-M0+ board support for the all-ops runner.
 *
 * The CM0+ is the boot core of the TRAVEO T2G: this image comes up first,
 * configures the device (clocks/pins/peripherals via the Device Configurator
 * output) and routes stdout to the KitProg3 UART (SCB0). It then runs the
 * SCALAR-path all-ops test itself and only releases Cortex-M7 core 0 from
 * board_all_ops_done(), i.e. after its own run is complete -- so the two test
 * runs appear sequentially on the single shared UART and never interleave.
 */

#include "cy_pdl.h"
#include "cycfg.h"

extern int retarget_stdio_init(void);

__attribute__((constructor)) static void board_init(void) {
  init_cycfg_all();       /* clocks, pins, peripherals (Device Configurator) */
  retarget_stdio_init();  /* stdout/stderr -> SCB0 (KitProg3 USB-UART)       */
}

/* Flash base of the CM7_0 application image. Must match
 * cm0plus_code_flash_reserve in xmc7200d_x8384_cm7_0.ld / _cm0plus.ld
 * (CM7_0 image is placed directly after the 3 MB CM0+ region). */
#define CM7_0_APP_VECTOR_TABLE_BASE 0x10300000u

/* Called by main() after the final "Test_result: SUMMARY" line: hand the UART
 * over to the M7 test. Register sequence as in the Arm-Examples
 * Safety-Example-Infineon-T2G CM0+ boot application. */
void board_all_ops_done(void) {
  uint32_t value;

  CPUSS->CM7_0_VECTOR_TABLE_BASE = CM7_0_APP_VECTOR_TABLE_BASE;
  CPUSS->CM7_0_PWR_CTL = (0x05FAu << CPUSS_UDB_PWR_CTL_VECTKEYSTAT_Pos) |
                         (3u << CPUSS_UDB_PWR_CTL_PWR_MODE_Pos);
  value = CPUSS->CM7_0_CTL;
  CPUSS->CM7_0_CTL = value & ~CPUSS_CM7_0_CTL_CPU_WAIT_Msk;
  value = SRSS->CLK_ROOT_SELECT[1];
  SRSS->CLK_ROOT_SELECT[1] = value | (1u << SRSS_CLK_ROOT_SELECT_ENABLE_Pos);
}
