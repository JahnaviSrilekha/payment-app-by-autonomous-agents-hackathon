# Acceptance tests

- `created_at_on_payment_responses` — R193 R194
- `seeded_created_at_semantics` — R195 R215 R197
- `seeded_future_created_at_rejected_atomically` — R196 R218
- `me_as_of_validation` — R198 R199 R252
- `me_as_of_semantics` — R201 R202 R203 R204 R216 R251
- `me_without_temporal_params_current` — R200 R259
- `statement_defaults_shape_window` — R205 R206 R207 R208 R209 R210 R211
- `statement_window_half_open` — R205 R207 R210
- `statement_ordering_ties_by_id` — R209
- `statement_pagination_full_window` — R206 R211 R212 R266
- `statement_visibility_own_only` — R213
- `statement_entry_revision_fields_uncorrected` — R214 R259
- `corrections_auth_and_lookup_errors` — R219 R220 R221 R245 R59 R63
- `corrections_body_validation` — R222 R223 R224 R225 R226 R227
- `correction_success_shape_revisions` — R214 R229 R230 R243
- `correction_stale_revision` — R231 R232
- `correction_replay_reuse_exactly_once` — R232 R233 R241 R3 R59
- `correction_preserves_parties_visibility_activity` — R228 R242
- `revisions_endpoint_access` — R243 R244 R245
- `correction_moves_money_conserves` — R234 R235 R240 R216
- `correction_insufficient_funds_preserves` — R236 R239 R285 R59
- `correction_historical_overdraft` — R237 R238 R239
- `correction_zero_amount_reversal` — R224 R257
- `correction_noop_same_amount` — design-19
- `known_at_validation_and_echo` — R246 R252 R253
- `known_at_selection_semantics` — R247 R248 R249 R254 R256
- `statement_known_at_window_and_combo` — R250 R251 R255 R258
- `snapshot_freeze_across_changes` — R260 R261 R262 R265 R290
- `snapshot_param_rules` — R263 R264 R267
- `correction_moves_payment_across_window` — R268 R290
- `concurrent_corrections_same_expected_revision` — R269 R231 R2
- `concurrent_same_key_correction_replay` — R3 R61 R232
- `settlement_member_correction_rejected` — R272 R271 R270 R99
- `capture_immutable_and_import_versions` — R273 R274 R275 A20
- `import_real_stage2_export` — R273 R274
- `me_historical_holds_lifecycle` — R276 R277 R278 R280 R281 R282 R279
- `seeded_holds_assumed_creation` — R286 R287
- `statement_only_money_movements` — R288 R289
- `correction_vs_holds_overdraft_precedence` — R284 R285 R236
- `new_accounts_open_at_zero` — R217 R216
- `historical_total_follows_revisions` — R283 R276
- `storm_stage3_invariants` — R1 R2 R3 R45 R239 R240 R269

# Requirement coverage

- **R1**: `storm_stage3_invariants`
- **R2**: `concurrent_corrections_same_expected_revision`, `storm_stage3_invariants`
- **R3**: `correction_replay_reuse_exactly_once`, `concurrent_same_key_correction_replay`, `storm_stage3_invariants`
- **A20**: `capture_immutable_and_import_versions`
- **R45**: `storm_stage3_invariants`
- **R59**: `corrections_auth_and_lookup_errors`, `correction_replay_reuse_exactly_once`, `correction_insufficient_funds_preserves`
- **R61**: `concurrent_same_key_correction_replay`
- **R63**: `corrections_auth_and_lookup_errors`
- **R99**: `settlement_member_correction_rejected`
- **R193**: `created_at_on_payment_responses`
- **R194**: `created_at_on_payment_responses`
- **R195**: `seeded_created_at_semantics`
- **R196**: `seeded_future_created_at_rejected_atomically`
- **R197**: `seeded_created_at_semantics`
- **R198**: `me_as_of_validation`
- **R199**: `me_as_of_validation`
- **R200**: `me_without_temporal_params_current`
- **R201**: `me_as_of_semantics`
- **R202**: `me_as_of_semantics`
- **R203**: `me_as_of_semantics`
- **R204**: `me_as_of_semantics`
- **R205**: `statement_defaults_shape_window`, `statement_window_half_open`
- **R206**: `statement_defaults_shape_window`, `statement_pagination_full_window`
- **R207**: `statement_defaults_shape_window`, `statement_window_half_open`
- **R208**: `statement_defaults_shape_window`
- **R209**: `statement_defaults_shape_window`, `statement_ordering_ties_by_id`
- **R210**: `statement_defaults_shape_window`, `statement_window_half_open`
- **R211**: `statement_defaults_shape_window`, `statement_pagination_full_window`
- **R212**: `statement_pagination_full_window`
- **R213**: `statement_visibility_own_only`
- **R214**: `statement_entry_revision_fields_uncorrected`, `correction_success_shape_revisions`
- **R215**: `seeded_created_at_semantics`
- **R216**: `me_as_of_semantics`, `correction_moves_money_conserves`, `new_accounts_open_at_zero`
- **R217**: `new_accounts_open_at_zero`
- **R218**: `seeded_future_created_at_rejected_atomically`
- **R219**: `corrections_auth_and_lookup_errors`
- **R220**: `corrections_auth_and_lookup_errors`
- **R221**: `corrections_auth_and_lookup_errors`
- **R222**: `corrections_body_validation`
- **R223**: `corrections_body_validation`
- **R224**: `corrections_body_validation`, `correction_zero_amount_reversal`
- **R225**: `corrections_body_validation`
- **R226**: `corrections_body_validation`
- **R227**: `corrections_body_validation`
- **R228**: `correction_preserves_parties_visibility_activity`
- **R229**: `correction_success_shape_revisions`
- **R230**: `correction_success_shape_revisions`
- **R231**: `correction_stale_revision`, `concurrent_corrections_same_expected_revision`
- **R232**: `correction_stale_revision`, `correction_replay_reuse_exactly_once`, `concurrent_same_key_correction_replay`
- **R233**: `correction_replay_reuse_exactly_once`
- **R234**: `correction_moves_money_conserves`
- **R235**: `correction_moves_money_conserves`
- **R236**: `correction_insufficient_funds_preserves`, `correction_vs_holds_overdraft_precedence`
- **R237**: `correction_historical_overdraft`
- **R238**: `correction_historical_overdraft`
- **R239**: `correction_insufficient_funds_preserves`, `correction_historical_overdraft`, `storm_stage3_invariants`
- **R240**: `correction_moves_money_conserves`, `storm_stage3_invariants`
- **R241**: `correction_replay_reuse_exactly_once`
- **R242**: `correction_preserves_parties_visibility_activity`
- **R243**: `correction_success_shape_revisions`, `revisions_endpoint_access`
- **R244**: `revisions_endpoint_access`
- **R245**: `corrections_auth_and_lookup_errors`, `revisions_endpoint_access`
- **R246**: `known_at_validation_and_echo`
- **R247**: `known_at_selection_semantics`
- **R248**: `known_at_selection_semantics`
- **R249**: `known_at_selection_semantics`
- **R250**: `statement_known_at_window_and_combo`
- **R251**: `me_as_of_semantics`, `statement_known_at_window_and_combo`
- **R252**: `me_as_of_validation`, `known_at_validation_and_echo`
- **R253**: `known_at_validation_and_echo`
- **R254**: `known_at_selection_semantics`
- **R255**: `statement_known_at_window_and_combo`
- **R256**: `known_at_selection_semantics`
- **R257**: `correction_zero_amount_reversal`
- **R258**: `statement_known_at_window_and_combo`
- **R259**: `me_without_temporal_params_current`, `statement_entry_revision_fields_uncorrected`
- **R260**: `snapshot_freeze_across_changes`
- **R261**: `snapshot_freeze_across_changes`
- **R262**: `snapshot_freeze_across_changes`
- **R263**: `snapshot_param_rules`
- **R264**: `snapshot_param_rules`
- **R265**: `snapshot_freeze_across_changes`
- **R266**: `statement_pagination_full_window`
- **R267**: `snapshot_param_rules`
- **R268**: `correction_moves_payment_across_window`
- **R269**: `concurrent_corrections_same_expected_revision`, `storm_stage3_invariants`
- **R270**: `settlement_member_correction_rejected`
- **R271**: `settlement_member_correction_rejected`
- **R272**: `settlement_member_correction_rejected`
- **R273**: `capture_immutable_and_import_versions`, `import_real_stage2_export`
- **R274**: `capture_immutable_and_import_versions`, `import_real_stage2_export`
- **R275**: `capture_immutable_and_import_versions`
- **R276**: `me_historical_holds_lifecycle`, `historical_total_follows_revisions`
- **R277**: `me_historical_holds_lifecycle`
- **R278**: `me_historical_holds_lifecycle`
- **R279**: `me_historical_holds_lifecycle`
- **R280**: `me_historical_holds_lifecycle`
- **R281**: `me_historical_holds_lifecycle`
- **R282**: `me_historical_holds_lifecycle`
- **R283**: `historical_total_follows_revisions`
- **R284**: `correction_vs_holds_overdraft_precedence`
- **R285**: `correction_insufficient_funds_preserves`, `correction_vs_holds_overdraft_precedence`
- **R286**: `seeded_holds_assumed_creation`
- **R287**: `seeded_holds_assumed_creation`
- **R288**: `statement_only_money_movements`
- **R289**: `statement_only_money_movements`
- **R290**: `snapshot_freeze_across_changes`, `correction_moves_payment_across_window`
- **design-19**: `correction_noop_same_amount`

## Requirement ids with no test

- **R265** (partially) — "no storage survival across container restarts is
  required" is a permission, not behaviour: nothing to assert black-box. The
  "tokens last until reset" half of the requirement is asserted by
  `snapshot_freeze_across_changes` and `snapshot_param_rules`.
- **R287** — "seeded closed holds need not reconstruct a prior lifecycle" is a
  scope limiter: absence of reconstruction cannot be asserted without risking
  a false failure of a permitted mechanism. Seeded closed holds are still
  exercised in `seeded_holds_assumed_creation` (not held at now).
- **design-19** (`correction_noop_same_amount`) is a design decision
  (stage-3 design §19 step 6), not a numbered requirement: a same-amount
  correction is valid and moves no money.

## Carried stage-1 invariants proven under stage-3 conditions

- **R1** conservation, **R2** no negative balance (including historical
  views), **R3** exactly-once idempotent writes (including correction
  replays), **R45** no 5xx under up to 50 concurrent requests — see
  `storm_stage3_invariants`, `concurrent_same_key_correction_replay`,
  `concurrent_corrections_same_expected_revision`, and the per-test
  conservation checks. Exact stage-1 §9 rounding is unchanged (integer minor
  units; every delta/balance assertion is exact).
