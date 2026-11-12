/* SPDX-License-Identifier: ISC */
#ifndef _WLAN_HDD_PASSTHRU_EXPERIMENT_H_
#define _WLAN_HDD_PASSTHRU_EXPERIMENT_H_
#include <linux/types.h>
#ifdef DRIVER_PASSTHRU_RUNTIME
bool hdd_pt_exclusive_mode(void);
bool hdd_pt_sta_coexist_mode(void);
bool hdd_pt_enabled_mode(void);
#else
static inline bool hdd_pt_exclusive_mode(void) { return false; }
static inline bool hdd_pt_sta_coexist_mode(void) { return false; }
static inline bool hdd_pt_enabled_mode(void) { return false; }
#endif
#endif
