/* SPDX-License-Identifier: MIT
 * The pinned Zephyr 4.1 nrfx resource header loads a controller reservation
 * table only for LL_SW_SPLIT. Use NCS's own SDC table for this receiver too.
 * This is selected through NRFX_RESERVED_RESOURCES_HEADER, for all nrfx users.
 */
#ifndef LISM_NRFX_RESERVED_RESOURCES_H_
#define LISM_NRFX_RESERVED_RESOURCES_H_

#if !defined(CONFIG_BT_LL_SOFTDEVICE) || !defined(CONFIG_SOC_COMPATIBLE_NRF52X)
#error "This resource adapter is only for the nRF52 SDC receiver"
#endif

/* Include the official lightweight resource declarations. The controller's
 * aggregate table includes mpsl_clock/HAL too early in nrfx_config processing.
 * Its group reservation is zero in this pinned nRF52 SDK (audited at build).
 */
#include <sdc_soc.h>
#include <mpsl_hwres.h>
#define NRFX_PPI_CHANNELS_USED_BY_BT_CTLR SDC_PPI_CHANNELS_USED_MASK
#define NRFX_PPI_CHANNELS_USED_BY_MPSL MPSL_RESERVED_PPI_CHANNELS

/* Retain Zephyr's remaining peripheral and resource reservation rules. */
#include <nrfx_reserved_resources.h>

#endif
