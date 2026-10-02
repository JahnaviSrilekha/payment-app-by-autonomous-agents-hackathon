# Pocketful stage 1 — acceptance coverage table

Black-box HTTP suite. Run with one command against a running service:

```sh
python3 specs/stage-1/acceptance/run.py http://localhost:8080
# or build + start the container and run:
sh specs/stage-1/acceptance/run.sh --docker 9090
```

Options: `--filter SUBSTR` (subset), `--wait N` (health wait), `--list`.

Every test derives from the stage-1 spec text (§1-§11) and the numbered requirements
R1-R101 — never from the implementation or the supplied checks. Tests run sequentially,
each after its own `POST /_test/reset`; concurrency happens inside tests. Endpoint groups
not yet merged are probed first and their tests are skipped with a reason.

| Requirement | Tests |
|---|---|
| R1 conservation | r45_no_5xx_under_50_concurrent, r61_concurrent_same_key_exactly_one_201, r61_concurrent_pay_same_key_once, r67_payment_atomic_no_trace_on_failure, r1_fuzz_mixed_operations_invariants, r2_concurrent_debits_never_negative, r31_exact_big_int_arithmetic, r84_splits_paid_conserve, r97_collective_not_sequential |
| R2 never negative | r45_no_5xx_under_50_concurrent, r1_fuzz_mixed_operations_invariants, r2_concurrent_debits_never_negative, r97_collective_not_sequential |
| R3 at most once | r61_concurrent_same_key_exactly_one_201, r61_concurrent_pay_same_key_once, r61_concurrent_settlement_same_key, r74_pay_replay_after_paid, r1_fuzz_mixed_operations_invariants |
| R4 minor units / existing wallets | r18_amount_integral_numeric, r17_one_currency_everywhere, r65_payment_create_schema |
| R5 delivery (Dockerfile, RUN.md) | static artifacts (reviewer); runtime behaviour via `run.sh --docker` |
| R6 standalone image, no runtime network | `run.sh --docker` (build + isolated run) |
| R7 resource limits | r7_r8_300_concurrent_requests_all_served, r45_no_5xx_under_50_concurrent, docker-mode 60s health; vCPU/memory/5s-timeout not black-box observable |
| R8 queue >= 256 | r7_r8_300_concurrent_requests_all_served |
| R9 PORT env, default 8080 | docker mode second container without `-e PORT` |
| R10 health | r10_health_ok |
| R11 reset seeds/replaces/repeats | r11_reset_seeds_state, r11_reset_replaces_and_repeats, r93_reset_clears_imported_state |
| R12 JSON + charset | r12_json_charset_content_type, r10_health_ok |
| R13 RFC3339 timestamps | r13_timestamps_rfc3339_offset (+ schemas in r65/r69/r99) |
| R14 unknown body fields | r14_unknown_body_fields_ignored, r58_same_key_different_path_ok, r96_entry_error_precedence |
| R15 unknown query params | r15_unknown_query_params_ignored |
| R16 ids opaque <= 64 | r16_ids_opaque_max64 |
| R17 one currency | r17_one_currency_everywhere |
| R18 integral numeric amounts | r18_amount_integral_numeric |
| R19 handle rules | r11_reset_seeds_state (seeded handle), r21_handle_derivation_rules, r52_derived_handle_collides |
| R20 seeded handle from fixture | r11_reset_seeds_state |
| R21 derived handle | r21_handle_derivation_rules, r52_derived_handle_collides |
| R22 new user balance 0, usable | r46_signup_schema_new_user_zero_balance |
| R23 payment moves atomically | r65_payment_create_schema, r67_payment_atomic_no_trace_on_failure |
| R24 request states + roles | r24_request_lifecycle_states, r75_decline_lifecycle, r76_cancel_lifecycle, r73_pay_error_table |
| R25 over-balance request legal | r25_request_over_balance_pending_then_payable |
| R26 visibility on payment, payer chooses | r26_payer_chooses_visibility_on_pay, r27_activity_feed_contract, r29_split_not_a_feed_item |
| R27 feed iff public or party | r27_activity_feed_contract, r81_activity_pagination |
| R28 /requests scope | r77_requests_listing_filters |
| R29 split not a feed item | r29_split_not_a_feed_item |
| R30 one visibility value both parties | r27_activity_feed_contract, r26_payer_chooses_visibility_on_pay |
| R31 amount <= 1e9, exact +-2^53 | r31_amount_bounds, r31_exact_big_int_arithmetic, r18_amount_integral_numeric |
| R32 fixture format | r11_reset_seeds_state |
| R33 seeded users login immediately | r11_reset_seeds_state, r86_import_roundtrip_preserves_all |
| R34 balances not replayed | r11_reset_seeds_state |
| R35 negative fixture -> 422, no change | r35_negative_fixture_balance_422 |
| R36 minor_units 0/2/3 | r17_one_currency_everywhere |
| R37 no admin balance endpoint | r37_no_admin_balance_endpoint |
| R38 error envelope | r38_error_envelope_shape (+ every is_err) |
| R39 status/code table | r39_status_code_matrix |
| R40 invalid format 422 / type 400 split | r40_wrong_type_400_vs_422 |
| R41 endpoint-specific precedence | r40_wrong_type_400_vs_422, r18_amount_integral_numeric, r65_payment_defaults |
| R42 integer query params plain digits | r42_query_integer_format |
| R43 400 reserved | r39_status_code_matrix, r40_wrong_type_400_vs_422 |
| R44 shared ranges | r44_key_length_range, r42_query_integer_format, r81_activity_pagination |
| R45 no 5xx ever | r45_no_5xx_under_50_concurrent, r1_fuzz_mixed_operations_invariants, r7_r8_300_concurrent_requests_all_served |
| R46 signup | r46_signup_schema_new_user_zero_balance |
| R47 login | r47_login_and_multiple_tokens |
| R48 email taken | r48_email_taken_409 |
| R49 password >= 8 | r49_password_min_length |
| R50 email shape | r50_email_shape |
| R51 bad credentials 401 | r51_login_bad_credentials, r11_reset_replaces_and_repeats |
| R52 handle taken | r52_derived_handle_collides |
| R53 bearer required elsewhere | r53_endpoints_require_auth, r94_operator_permissions (settlements) |
| R54 multiple tokens | r47_login_and_multiple_tokens |
| R55 password hashing | N/A black-box (design + ADR-002) |
| R56 five keyed paths | r56_missing_key_on_five_paths |
| R57 key scoped to user | r57_key_scoped_per_user |
| R58 same key other path not a replay | r58_same_key_different_path_ok |
| R59 idempotency table | r59_idempotency_lifecycle, r72_pay_body_and_replay_sensitivity, r44_key_length_range |
| R60 same JSON value | r59_idempotency_lifecycle |
| R61 concurrent exactly once | r61_concurrent_same_key_exactly_one_201, r61_concurrent_pay_same_key_once, r61_concurrent_settlement_same_key |
| R62 replay after state change | r62_replay_after_state_change, r74_pay_replay_after_paid, r100_settlement_replay_and_visibility |
| R63 claimed key before validation | r63_claimed_key_before_field_validation |
| R64 GET /me | r11_reset_seeds_state, r65_payment_create_schema |
| R65 POST /payments schema + defaults | r65_payment_create_schema, r65_payment_defaults |
| R66 payments error table | r66_payment_errors, r31_amount_bounds |
| R67 atomic debit+credit | r67_payment_atomic_no_trace_on_failure, r2_concurrent_debits_never_negative |
| R68 note verbatim | r68_note_verbatim_unicode, r66_payment_errors (200-char boundary) |
| R69 POST /requests schema | r69_request_create_schema |
| R70 requests error table | r70_request_errors |
| R71 no balance check at creation | r25_request_over_balance_pending_then_payable |
| R72 pay endpoint + body sensitivity | r72_pay_body_and_replay_sensitivity, r26_payer_chooses_visibility_on_pay |
| R73 pay error table | r73_pay_error_table |
| R74 pay replay after paid | r74_pay_replay_after_paid, r61_concurrent_pay_same_key_once |
| R75 decline | r75_decline_lifecycle |
| R76 cancel | r76_cancel_lifecycle |
| R77 /requests filters + pagination | r77_requests_listing_filters, r81_activity_pagination |
| R78 splits schema/order | r78_split_forms_and_order, r82_rounding_table_exact |
| R79 splits error table | r79_split_errors |
| R80 caller-only split, no balance check | r80_split_caller_only_valid |
| R81 /activity pagination | r81_activity_pagination |
| R82 rounding table | r82_rounding_table_exact |
| R83 reorder moves extra unit; 0-share | r83_reorder_moves_extra_unit, r83_zero_share_still_requests |
| R84 splits conserve | r84_splits_paid_conserve |
| R85 export shape | r85_export_shape |
| R86 import round trip | r86_import_roundtrip_preserves_all, r101_settlement_preserved_by_import |
| R87 replacement, no duplication | r87_import_replacement_repeats |
| R88 import errors, no change | r88_import_invalid_no_change |
| R89 10s test-control timeout | N/A black-box |
| R90 export snapshot immutable | r90_export_snapshot_immutable |
| R91 preservation across import | r86_import_roundtrip_preserves_all, r101_settlement_preserved_by_import |
| R92 import removes destination | r92_import_removes_destination_credentials, r87_import_replacement_repeats |
| R93 reset clears imported state | r93_reset_clears_imported_state, r92_import_removes_destination_credentials |
| R94 operator permissions | r94_operator_permissions |
| R95 settlements auth/key/shape | r95_settlement_auth_key_shape |
| R96 entry-error precedence | r96_entry_error_precedence |
| R97 collective affordability | r97_collective_not_sequential |
| R98 all-or-nothing, key not claimed | r98_settlement_all_or_nothing, r97_collective_not_sequential |
| R99 settlement response shape | r99_settlement_response_shape |
| R100 replay + constituent visibility | r100_settlement_replay_and_visibility |
| R101 settlements preserved by import | r101_settlement_preserved_by_import |
| R102 no directory/user-search endpoints | r102_r103_no_out_of_scope_endpoints |
| R103 no email-verification/password-reset/refresh/role endpoints | r102_r103_no_out_of_scope_endpoints |

## Recorded assumptions exercised by the suite

- A2 (from requirements.md): entry errors are per-entry checks in input order, before the
  collective affordability check — r96 asserts this directly.
- 401 precedes 400 missing_idempotency_key when both apply (design §5 order; spec silent) —
  asserted in r39 via no-token-no-key ordering cases.
- Paying a 0-share request would be an amount-0 payment (422 per payment rules); the spec
  only claims the request is created, so r83_zero_share_still_requests asserts creation only.
- Export body compared as parsed JSON where possible; byte-compare used only for
  snapshot-equality checks (r87, r90).
- A6 (from requirements.md): 401 unauthenticated precedes 400 missing_idempotency_key when
  both apply — asserted in r53 (POST /payments with neither token nor key → 401).
- A7 (from requirements.md): Idempotency-Key >255 chars → 422 validation_failed and never
  claims or consults a record (no false 409) — asserted in r44_key_length_range.
- Import of a structurally invalid `state` (no recognizable users) is expected to 422 per
  "an invalid state give 422" (r88).