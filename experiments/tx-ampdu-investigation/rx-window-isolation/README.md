# RX initial REO-window isolation (2026-11-11)

Patch is based on a read-only copy of build-host /work/houji-build/src/three-findings/wlan/qca-wifi-host-cmn/dp/wifi3.0/dp_rx_tid.c. No shared code or device changed.

## Behavior

`pt_remote_rx_window=64` preserves the successful reo2 configuration. Values 1, 64, 256 are accepted only at module load (0400); invalid values reject parameter parsing. The existing PASSTHRU + reo_mode==2 TID0 setup consumes the value, bounded by named HAL maximum, and logs requested/effective/max. Other peers, non-QoS TID, routing, peer creation, teardown, and TX retry policy remain unchanged. No asynchronous command, runtime setter access, or new vdev pointer lifetime is introduced. Kiwi HAL advertises maximum1024 in hal_kiwi.c, so all three requested values fit this target.

This changes the **initial host REO queue window**, not advertised/negotiated air BlockAck window. Existing dp_addba_requestprocess_wifi3 may subsequently update ba_win_size, SSN, and BA status. A later FW/host BA update can overwrite this initial choice; performance similarity would not prove the parameter never applied.

## Suggested experiment

Keep EZIF, reo2, tx_ampdu1, tx-addba win64 and the current successful module configuration otherwise fixed. Compare initial RX window64 ->1 ->64, then256 if useful. Verify PT_RX_WINDOW receipt, bidirectional SHA, RX handler errors, hardware aggregation, directional throughput. Compare gap distributions only over transfer intervals; the current final OWLY_RX_GAPS includes both directions and idle time. A window1 result is not an AMPDU-off result.

## Evidence and limits

Successful on64 run's final RX gap row: matched57740, missing0, equal56049, backwards0, host_delayed0, host_caught_up0. This does not show the measured class of host delivery stalls; it cannot distinguish RF loss, AWDL schedule, firmware buffering/reordering, or TCP/app idle. Its >128ms buckets include idle between tests, so their total duration cannot be assigned to RX throughput loss.

Owly logs req0/resp0/tx_req4/rx_resp0. No userland-validated ADDBA response exists. EZIF hardware TX aggregation proves functionality, not which component performed or consumed action-frame negotiation. Don't infer FW response consumption as confirmed from missing userland packets alone.

## Validation

Extracted actual parameter setter compiled by Clang -Wall -Wextra -Werror + ASan/UBSan; 3 accepted values and10 rejection cases passed, preserving old value on reject. kstrtouint was mocked; this does not validate kernel headers/preprocessor or real target build. Root must compile and audit the integrated target module before testing. hashes.json records exact base/work hashes. Default64 preserves prior queue argument; patch changes no interface declarations in dp_rx.h.
