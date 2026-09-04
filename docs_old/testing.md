# テスト文書

本ツールのテストは 2 層で構成されています。

| 層 | 内容 | 場所 |
|---|---|---|
| ユニット / エンドツーエンドテスト | Python 標準の `unittest` による自動テスト。全モジュールと、同梱サンプル設定の変換結果を検証 | `tests/` |
| 実機検証 | 生成した EXOS 設定を Extreme 実機に投入し、受理・反映・制約を確認 | 本書 5 章、[pol-acl-hw-test.md](pol-acl-hw-test.md)、[mirror-hw-test.md](mirror-hw-test.md) |

## 1. 自動テストの概要

追加パッケージは不要です。テストコードは対象モジュールごとにファイルを分けています。

| テストファイル | 対象 | テスト数 |
|---|---|---|
| `tests/test_helpers.py` | `helpers.py` (VLAN リスト、インターフェース名、範囲展開、自然順、アドレス変換) | 19 |
| `tests/test_scanner.py` | `scanner.py` (ブロック分割、行番号、コメント除去) | 7 |
| `tests/test_parser.py` | `parser.py` (グローバル、VLAN、インターフェース、Port-channel、ACL、SPAN の解釈) | 30 |
| `tests/test_validation.py` | `validation.py` (相互参照チェックと警告) | 8 |
| `tests/test_mapping.py` | `mapping.py` (マッピングファイルの読み書きとマージ) | 6 |
| `tests/test_generator.py` | `generator.py` (EXOS 出力、ポリシーファイル、ミラー、警告バナー、スタック手順書) | 42 |
| `tests/test_cli.py` | `cli.py` (コマンドライン、出力ファイル、終了コード) | 7 |
| `tests/test_samples.py` | 同梱サンプル設定 4 件のエンドツーエンド変換 | 6 |
| 合計 | | **125** |

## 2. 実行方法

リポジトリ直下で実行します。

全テストを実行:

```bash
python3 -m unittest
```

詳細表示で実行:

```bash
python3 -m unittest -v
```

特定ファイルだけ実行:

```bash
python3 -m unittest tests.test_generator -v
```

特定のテストクラス / メソッドだけ実行:

```bash
python3 -m unittest tests.test_generator.FilteredMirrorTests.test_filter_policy_bound_before_enable -v
```

手動での動作確認 (サンプル設定を変換し、生成物を確認):

```bash
python3 main.py sample.cfg
```

## 3. 各テストの検証内容

### 3.1 helpers (`tests/test_helpers.py`)

| テストクラス | 検証内容 |
|---|---|
| `ParseVlanListTests` | `"10,20,30-32"` の展開、空白や末尾カンマの許容、不正値 (空、文字列、逆順、0、4095) で `ValueError` |
| `CanonicalizeInterfaceNameTests` | Gi / Te / Tw / Fi / Twe / Fo / Hu / Fa / Po と `port-channel 1` の正規化、大文字小文字の統一、先頭の `interface ` 除去、`Vlan10` などはそのまま |
| `ParseInterfaceIdentityTests` | `Gi1/0/1` → メンバー 1、モジュール 0、ポート 1。2 段表記や論理インターフェースは番号なし。Port-channel ID の抽出 |
| `ExpandInterfaceRangeTests` | 範囲展開、省略形と個別指定の混在、`, ` 区切りと ` - ` 表記、重複除去と自然順、不正入力 |
| `InterfaceSortKeyTests` | 物理ポート (種別 → メンバー → ポート番号の数値順) → Port-channel → その他、の自然順 |
| `AddressHelperTests` | ワイルドカードマスク / ネットマスクから CIDR への変換、不連続マスクや不正値は `None` |

### 3.2 scanner (`tests/test_scanner.py`)

| テスト | 検証内容 |
|---|---|
| `test_blocks_and_line_numbers` | global / vlan / interface / global の順にブロック化され、元の行番号を保持 |
| `test_comment_lines_are_dropped` | `!` と `! コメント` の両方を除去 |
| `test_interface_range_kind` | `interface range` は `interface_range` 種別 |
| `test_acl_block_kind` | `ip access-list extended` は `acl` 種別 |
| `test_vlan_global_commands_are_not_blocks` | `vlan internal allocation policy` は VLAN ブロックではなくグローバル行 |
| `test_indented_line_without_header_is_global` | 見出しのないインデント行はグローバル行 |
| `test_empty_input` | 空入力で空リスト |

### 3.3 parser (`tests/test_parser.py`)

| テストクラス | 検証内容 |
|---|---|
| `GlobalTests` | ホスト名、`switch N provision / priority`、ポート名からのスタックメンバー推定、`end` と `ip routing` の消費、未対応グローバル行の行番号保持、スタティックルート (不正マスクと `Null0` は未対応行) |
| `VlanTests` | リスト / 範囲 / 引用符付き名前、不正な VLAN ヘッダーの報告、未対応サブコマンドの報告 |
| `InterfaceTests` | アクセスポート、`shutdown` / `no shutdown` の後勝ち、`interface range` 展開、不正範囲の報告、同一ポートの 2 ブロック統合、トランク許可リストの add / remove とネイティブ VLAN、明示リストなし add / remove の警告、`allowed vlan all / except` は未対応行、ルーテッドポート (`no switchport`、`ip address dhcp`、`secondary`)、SVI アドレス、`ip access-group in / out` |
| `PortChannelTests` | メンバーの自然順結合と Port-channel 設定の取り込み、`interface Port-channelN` ブロックなしでの生成、Port-channel 自身への `channel-group` は未対応行 |
| `AclTests` | 名前付き extended ACL (remark、シーケンス番号、host / wildcard、eq / range、プロトコル番号)、番号付き standard / extended ACL と範囲外番号、送信元ポート、未対応 ACE (`established`、`log`、`eq www`、`gt`、不連続マスク、`ospf`、`evaluate`) の理由付き報告と「不完全 ACL」警告 |
| `MonitorSessionTests` | ローカル SPAN (範囲付きポートリスト、方向の既定値 both、Port-channel ソース、VLAN ソースの範囲、`encapsulation replicate`)、フィルタ ACL と重複指定の拒否、RSPAN / ERSPAN / 他のフィルタ形式 / 論理インターフェースの報告 |

### 3.4 validation (`tests/test_validation.py`)

| テストクラス | 検証内容 |
|---|---|
| `VlanReferenceTests` | 未定義のアクセス / トランク許可 / ネイティブ VLAN (ネイティブ 1 は除外)、access / trunk モードの矛盾、警告の自然順 |
| `RoutedAndBundleTests` | ルーテッドポートは警告、IP 付き SVI は警告しない、メンバーなし Port-channel、Port-channel の VLAN 警告は 1 回だけ |
| `MonitorSessionTests` | ソースまたは宛先のないセッション、ソースと宛先の重複、未定義ソース VLAN、未定義フィルタ ACL |

### 3.5 mapping (`tests/test_mapping.py`)

| テストクラス | 検証内容 |
|---|---|
| `FileTests` | JSON の書き出しと読み込み、不正 JSON / 配列は `ValueError` |
| `MergeTests` | 利用者の値が優先、ファイルにない項目は既定値で補い警告、Cisco 設定にない項目は無視して警告、`lags` の項目単位マージ、オブジェクトでないセクションの無視、既定値を破壊しない |

### 3.6 generator (`tests/test_generator.py`)

| テストクラス | 検証内容 |
|---|---|
| `DefaultMappingTests` | マッピングの構成 (vlans / ports / uplinks / lags / mirrors / mirror_egress_mode)、ポートの自然順、LAG マスターが最小番号、SVI はポートに含めない |
| `VlanTests` | VLAN 名の整形 (記号→`_`、数字始まり→`V_`、32 文字切り詰め、重複はタグ付与)、VLAN 1 は `Default` で作成しない、未定義 VLAN の自動作成、マッピングによる改名と不正キーの警告 |
| `PortTests` | アクセスポートの出力行 (description、Default からの削除、untagged 追加、disable)、トランク (ネイティブ untagged、許可 VLAN tagged、ネイティブは tagged にしない)、許可リストなしトランクの全 VLAN 展開と警告、設定のないポートは出力しない、ルーテッドポートのスキップ、ポートの自然順 |
| `StackAndUplinkTests` | `slot:port` 表記とスタック確認コメント、アップリンクのプレースホルダと `uplinks.start` による解決、不正な `start` の無視、ポート番号の重複警告、番号を導けないポート名の警告、スタック構築手順書の内容、単体構成では手順書なし |
| `LagTests` | LACP / 静的 / PAgP の出力、混在モードの警告、マスターにのみ VLAN 設定、メンバーの description と shutdown の保持、マッピングによるマスター / モードの上書きと不正値の警告、メンバーなし Port-channel のスキップ |
| `L3Tests` | SVI の IP アドレスと `enable ipforwarding`、SVI のみの VLAN の自動作成、shutdown された SVI のスキップ、デフォルトルートと通常ルート |
| `AclTests` | `.pol` の内容 (remark コメント、エントリ順、`deny ip any any` と暗黙 deny の IPv4 限定マッチ)、ポート / SVI (VLAN) / Port-channel マスターへの適用、未定義 ACL・未適用 ACL・ルーテッドポート上の ACL の警告、standard ACL とポート範囲 |
| `MirrorTests` | ミラーインスタンスの出力順 (Default 削除 → create → to port → add port / vlan → enable)、宛先ポートは description のみ保持、`DefaultMirror` 再利用と名前の整形、`encapsulation replicate` なしの警告、複数セッションの警告、Port-channel ソースの展開、LAG メンバー / Port-channel 宛先のスキップ、複数宛先の `port-list` |
| `FilteredMirrorTests` | フィルタ ACL の適用が `enable mirror` より前、`.pol` の `mirror` アクション (Cisco の deny は permit のみ)、`whole-port` モード、不正な egress モードの警告、VLAN ソースはフィルタなし、ルールのないフィルタ ACL はポート全体をミラー |
| `BannerTests` | WARNINGS の 3 分類と Translation reference の内容、警告のない設定にはバナーなし、未対応行一覧の上限 (40 種類) 超過時の省略表示 |

### 3.7 CLI (`tests/test_cli.py`)

| テスト | 検証内容 |
|---|---|
| `test_no_args_prints_usage` | 引数なしで終了コード 1 と書式表示 |
| `test_missing_file` | 存在しないファイルで終了コード 1 とエラーメッセージ |
| `test_first_run_writes_mapping_xsf_and_pol` | 初回実行で `.map.json`、`.xsf`、`-acls/*.pol` を生成 |
| `test_second_run_applies_mapping_edits_and_keeps_the_file` | 2 回目はマッピングの編集 (VLAN 名、ポート番号) が反映され、マッピングファイルは上書きされない |
| `test_invalid_mapping_json_fails` | 不正な JSON のマッピングで終了コード 1 |
| `test_stack_runbook_and_warning_summary` | スタック構成で `.stack-setup.txt` を生成、標準エラーに警告件数の要約 |
| `test_multiple_files` | 複数ファイルをそれぞれ変換 |

### 3.8 サンプル設定 (`tests/test_samples.py`)

同梱のサンプル設定を丸ごと変換し、代表的な出力行と警告を確認します。
同じ入力から常に同じ出力が得られること (決定性) も検証します。

| サンプル | 特徴 | 主な確認点 |
|---|---|---|
| `sample.cfg` | 2 台スタック、LAG、アップリンク、SPAN + FSPAN | 解析結果と警告 4 件、`slot:port` 表記、sharing、ミラー 2 セッション、`monitor_2_filter.pol`、スタック手順書 |
| `demo.cfg` | 単体構成、未対応行 (voice vlan、portfast、storm-control) | LAG マスターが 9、Not translated の集計、`end` を報告しない |
| `stack-demo.cfg` | スロットをまたぐ LAG | `1:10,2:10` の sharing、メンバーの description 保持、スタック手順書 |
| `docs/cisco-acl-list.cfg` | ACL の全パターン | `SERVERS_IN.pol` と `V_100.pol`、ポート / VLAN への適用、未対応 ACE の報告、空マッチのエントリがないこと |

## 4. 最新の自動テスト結果

| 項目 | 値 |
|---|---|
| 実施日 | 2026-09-04 |
| 環境 | macOS 26.6.2, Python 3.9.6 |
| コマンド | `python3 -m unittest -v` |
| 結果 | **125 件すべて成功 (OK)** |

実行ログ全文:

```
test_first_run_writes_mapping_xsf_and_pol (tests.test_cli.CliTests) ... ok
test_invalid_mapping_json_fails (tests.test_cli.CliTests) ... ok
test_missing_file (tests.test_cli.CliTests) ... ok
test_multiple_files (tests.test_cli.CliTests) ... ok
test_no_args_prints_usage (tests.test_cli.CliTests) ... ok
test_second_run_applies_mapping_edits_and_keeps_the_file (tests.test_cli.CliTests) ... ok
test_stack_runbook_and_warning_summary (tests.test_cli.CliTests) ... ok
test_acl_on_bundle_applies_to_master (tests.test_generator.AclTests) ... ok
test_policy_file_and_port_apply (tests.test_generator.AclTests) ... ok
test_router_acl_on_svi_applies_to_vlan_with_warning (tests.test_generator.AclTests) ... ok
test_standard_acl_and_port_ranges (tests.test_generator.AclTests) ... ok
test_unapplied_undefined_and_routed_acls_warn (tests.test_generator.AclTests) ... ok
test_clean_config_has_no_banner (tests.test_generator.BannerTests) ... ok
test_unsupported_summary_truncates (tests.test_generator.BannerTests) ... ok
test_warning_groups_and_reference (tests.test_generator.BannerTests) ... ok
test_structure_and_natural_order (tests.test_generator.DefaultMappingTests) ... ok
test_svis_are_not_ports (tests.test_generator.DefaultMappingTests) ... ok
test_filter_acl_without_rules_mirrors_whole_port (tests.test_generator.FilteredMirrorTests) ... ok
test_filter_policy_bound_before_enable (tests.test_generator.FilteredMirrorTests) ... ok
test_invalid_egress_mode_falls_back (tests.test_generator.FilteredMirrorTests) ... ok
test_vlan_sources_are_unfiltered (tests.test_generator.FilteredMirrorTests) ... ok
test_whole_port_egress_mode (tests.test_generator.FilteredMirrorTests) ... ok
test_svi_and_routes (tests.test_generator.L3Tests) ... ok
test_empty_bundle_skipped (tests.test_generator.LagTests) ... ok
test_lacp_bundle_config_on_master (tests.test_generator.LagTests) ... ok
test_mapping_overrides_master_and_mode (tests.test_generator.LagTests) ... ok
test_mixed_modes_warn (tests.test_generator.LagTests) ... ok
test_static_and_pagp (tests.test_generator.LagTests) ... ok
test_default_mirror_reuse_and_rename (tests.test_generator.MirrorTests) ... ok
test_encapsulation_warning_and_multiple_sessions (tests.test_generator.MirrorTests) ... ok
test_mirror_instance_lines_in_order (tests.test_generator.MirrorTests) ... ok
test_multiple_destinations_use_port_list (tests.test_generator.MirrorTests) ... ok
test_port_channel_source_expands_and_lag_destination_skips (tests.test_generator.MirrorTests) ... ok
test_access_port (tests.test_generator.PortTests) ... ok
test_hostname_and_empty_port_skipped (tests.test_generator.PortTests) ... ok
test_ports_in_natural_order (tests.test_generator.PortTests) ... ok
test_routed_port_is_skipped (tests.test_generator.PortTests) ... ok
test_trunk_with_allowed_list_and_native (tests.test_generator.PortTests) ... ok
test_trunk_without_allowed_list_expands_with_warning (tests.test_generator.PortTests) ... ok
test_invalid_uplink_start_is_ignored (tests.test_generator.StackAndUplinkTests) ... ok
test_port_collision_is_flagged (tests.test_generator.StackAndUplinkTests) ... ok
test_stack_setup_runbook (tests.test_generator.StackAndUplinkTests) ... ok
test_stacked_slot_port_naming_and_review_comments (tests.test_generator.StackAndUplinkTests) ... ok
test_unparseable_port_name_is_emitted_verbatim_with_warning (tests.test_generator.StackAndUplinkTests) ... ok
test_uplink_placeholder_and_resolution (tests.test_generator.StackAndUplinkTests) ... ok
test_mapping_renames_vlan (tests.test_generator.VlanTests) ... ok
test_names_are_sanitized_and_deduped (tests.test_generator.VlanTests) ... ok
test_referenced_undefined_vlan_is_auto_created (tests.test_generator.VlanTests) ... ok
test_vlan_1_is_default_and_never_created (tests.test_generator.VlanTests) ... ok
test_netmask_to_cidr (tests.test_helpers.AddressHelperTests) ... ok
test_wildcard_to_cidr (tests.test_helpers.AddressHelperTests) ... ok
test_abbreviations (tests.test_helpers.CanonicalizeInterfaceNameTests) ... ok
test_full_names_are_case_normalized (tests.test_helpers.CanonicalizeInterfaceNameTests) ... ok
test_interface_keyword_is_stripped (tests.test_helpers.CanonicalizeInterfaceNameTests) ... ok
test_unknown_types_pass_through (tests.test_helpers.CanonicalizeInterfaceNameTests) ... ok
test_abbreviated_and_mixed (tests.test_helpers.ExpandInterfaceRangeTests) ... ok
test_invalid_inputs_raise (tests.test_helpers.ExpandInterfaceRangeTests) ... ok
test_natural_sort_and_dedup (tests.test_helpers.ExpandInterfaceRangeTests) ... ok
test_simple_range (tests.test_helpers.ExpandInterfaceRangeTests) ... ok
test_spaces_after_commas_and_around_dash (tests.test_helpers.ExpandInterfaceRangeTests) ... ok
test_natural_order_across_types_members_and_bundles (tests.test_helpers.InterfaceSortKeyTests) ... ok
test_logical_interfaces (tests.test_helpers.ParseInterfaceIdentityTests) ... ok
test_port_channel_id (tests.test_helpers.ParseInterfaceIdentityTests) ... ok
test_stacked_numbering (tests.test_helpers.ParseInterfaceIdentityTests) ... ok
test_two_part_names_have_no_numbering (tests.test_helpers.ParseInterfaceIdentityTests) ... ok
test_invalid_inputs_raise (tests.test_helpers.ParseVlanListTests) ... ok
test_mixed_list_and_ranges (tests.test_helpers.ParseVlanListTests) ... ok
test_whitespace_and_trailing_comma (tests.test_helpers.ParseVlanListTests) ... ok
test_invalid_json_and_non_object (tests.test_mapping.FileTests) ... ok
test_roundtrip (tests.test_mapping.FileTests) ... ok
test_lag_entries_merge_per_field (tests.test_mapping.MergeTests) ... ok
test_non_object_section_is_ignored (tests.test_mapping.MergeTests) ... ok
test_unknown_entries_are_ignored_with_a_note (tests.test_mapping.MergeTests) ... ok
test_user_values_win_and_missing_entries_are_noted (tests.test_mapping.MergeTests) ... ok
test_named_extended_acl (tests.test_parser.AclTests) ... ok
test_numbered_acls (tests.test_parser.AclTests) ... ok
test_source_port_on_extended_acl (tests.test_parser.AclTests) ... ok
test_unsupported_aces_are_reported_not_dropped (tests.test_parser.AclTests) ... ok
test_end_and_ip_routing_are_consumed (tests.test_parser.GlobalTests) ... ok
test_hostname_and_stack (tests.test_parser.GlobalTests) ... ok
test_stack_members_inferred_from_port_names (tests.test_parser.GlobalTests) ... ok
test_static_routes (tests.test_parser.GlobalTests) ... ok
test_unknown_global_lines_are_kept_with_line_numbers (tests.test_parser.GlobalTests) ... ok
test_access_group (tests.test_parser.InterfaceTests) ... ok
test_access_port (tests.test_parser.InterfaceTests) ... ok
test_add_remove_without_explicit_list_warns (tests.test_parser.InterfaceTests) ... ok
test_bad_range_is_reported (tests.test_parser.InterfaceTests) ... ok
test_interface_range_expansion (tests.test_parser.InterfaceTests) ... ok
test_no_shutdown_after_shutdown (tests.test_parser.InterfaceTests) ... ok
test_routed_ports (tests.test_parser.InterfaceTests) ... ok
test_same_interface_in_two_blocks_merges (tests.test_parser.InterfaceTests) ... ok
test_svi_address (tests.test_parser.InterfaceTests) ... ok
test_trunk_allowed_add_remove_and_native (tests.test_parser.InterfaceTests) ... ok
test_trunk_allowed_keywords_are_unsupported (tests.test_parser.InterfaceTests) ... ok
test_unknown_interface_lines_are_kept (tests.test_parser.InterfaceTests) ... ok
test_filter_acl (tests.test_parser.MonitorSessionTests) ... ok
test_local_span (tests.test_parser.MonitorSessionTests) ... ok
test_rspan_erspan_and_other_forms_are_reported (tests.test_parser.MonitorSessionTests) ... ok
test_bundle_created_without_port_channel_block (tests.test_parser.PortChannelTests) ... ok
test_channel_group_on_bundle_is_unsupported (tests.test_parser.PortChannelTests) ... ok
test_members_link_in_natural_order (tests.test_parser.PortChannelTests) ... ok
test_bad_vlan_header_is_reported (tests.test_parser.VlanTests) ... ok
test_lists_ranges_and_names (tests.test_parser.VlanTests) ... ok
test_unknown_vlan_subcommand_is_reported (tests.test_parser.VlanTests) ... ok
test_translation (tests.test_samples.AclListCfgTests) ... ok
test_translation (tests.test_samples.DemoCfgTests) ... ok
test_deterministic (tests.test_samples.SampleCfgTests) ... ok
test_generated_script (tests.test_samples.SampleCfgTests) ... ok
test_parse (tests.test_samples.SampleCfgTests) ... ok
test_translation (tests.test_samples.StackDemoCfgTests) ... ok
test_acl_block_kind (tests.test_scanner.ScanConfigTests) ... ok
test_blocks_and_line_numbers (tests.test_scanner.ScanConfigTests) ... ok
test_comment_lines_are_dropped (tests.test_scanner.ScanConfigTests) ... ok
test_empty_input (tests.test_scanner.ScanConfigTests) ... ok
test_indented_line_without_header_is_global (tests.test_scanner.ScanConfigTests) ... ok
test_interface_range_kind (tests.test_scanner.ScanConfigTests) ... ok
test_vlan_global_commands_are_not_blocks (tests.test_scanner.ScanConfigTests) ... ok
test_incomplete_sessions (tests.test_validation.MonitorSessionTests) ... ok
test_overlap_undefined_vlan_and_missing_filter (tests.test_validation.MonitorSessionTests) ... ok
test_bundle_vlan_warning_once (tests.test_validation.RoutedAndBundleTests) ... ok
test_empty_port_channel (tests.test_validation.RoutedAndBundleTests) ... ok
test_routed_port_warned_but_addressed_svi_is_not (tests.test_validation.RoutedAndBundleTests) ... ok
test_mode_conflicts (tests.test_validation.VlanReferenceTests) ... ok
test_undefined_access_trunk_and_native_vlans (tests.test_validation.VlanReferenceTests) ... ok
test_warnings_follow_natural_port_order (tests.test_validation.VlanReferenceTests) ... ok

----------------------------------------------------------------------
Ran 125 tests in 0.020s

OK
```

### 4.1 サンプル設定の変換結果

`python3 main.py sample.cfg demo.cfg stack-demo.cfg docs/cisco-acl-list.cfg` を実行した結果です。
すべて終了コード 0 で、次のファイルが生成されました。

| 入力 | 生成物 | 警告 (Input + Translation) | 未対応行 |
|---|---|---|---|
| `sample.cfg` | `.map.json`, `.xsf`, `sample-acls/monitor_2_filter.pol`, `.stack-setup.txt` | 17 | 0 |
| `demo.cfg` | `.map.json`, `.xsf` | 1 | 27 |
| `stack-demo.cfg` | `.map.json`, `.xsf`, `.stack-setup.txt` | 3 | 16 |
| `docs/cisco-acl-list.cfg` | `.map.json`, `.xsf`, `cisco-acl-list-acls/SERVERS_IN.pol`, `V_100.pol` | 5 | 6 |

`sample.cfg` は意図的に「未定義 VLAN 99 のアクセスポート」「未定義 VLAN 31 / 32 のトランク」
「ルーテッドポート」「アップリンクモジュールのポート」を含んでおり、これらの警告が出るのが正しい状態です。
`demo.cfg` と `stack-demo.cfg` の未対応行は `spanning-tree portfast`、`switchport voice vlan`、
`storm-control`、`ntp server`、`snmp-server location` で、いずれも本ツールの対象外として
「Not translated」に集計されます。

## 5. 実機検証

生成した EXOS 設定を Extreme 実機に投入し、構文の受理、ハードウェアへの反映、
機種固有の制約を確認しました。データポートにケーブルを接続できない環境だったため、
トラフィックを流しての確認は行っていません (設定レベルの検証)。

### 5.1 検証環境と方針

| 項目 | 内容 |
|---|---|
| 機器 ① | Extreme Networks X440G2-12p-10G4 (スタンドアロン)、EXOS 33.4.1.15 |
| 機器 ② | X440G2 × 2 台 SummitStack (リング型、スロット 1 = Master、スロット 2 = Backup) |
| 接続 | 各機器の管理ポートへ telnet (データポートとは経路分離) |
| 実施日 | ACL: 2026-08-12、ミラーリング: 2026-08-26 〜 27 |

- 未使用ポートのみ使用し、既存の通信・VLAN には変更を加えない。
- 設定変更は都度ロールバックし、`show ports <範囲> vlan` でポート単位の VLAN 所属を
  検証前のスナップショットと突合してから終了する。
- `save configuration` は一切実行しない。

### 5.2 検証結果一覧

| No | 検証項目 | 結果 |
|---|---|---|
| 1 | ACL: 生成した `.pol` (名前付き extended、番号付き extended) の `check policy` とポートへの適用 | ○ |
| 2 | 通常ミラーリング: 複数ポート、rx / tx / both、VLAN ソースを含む構成 (スタンドアロン) | ○ |
| 3 | 通常ミラーリング: スロットをまたぐモニター / ソース配置 (スタック、双方向) | ○ |
| 4 | FSPAN: 生成した `.pol` をそのままアップロードして適用 | ○ |
| 5 | whole-port egress フォールバック: ingress = ACL、egress = ポート全体を同一インスタンスで適用 | ○ |

### 5.3 ACL (検証項目 1)

`docs/cisco-acl-list.cfg` から生成した `SERVERS_IN.pol` と `V_100.pol` をスイッチに置き、
`check policy` で構文検証したうえで `.xsf` の行をそのまま投入しました。

```
check policy SERVERS_IN                          -> Policy file check successful.
check policy V_100                               -> Policy file check successful.
configure access-list SERVERS_IN ports 1 ingress -> done!
configure access-list V_100 ports 2 ingress      -> done!
```

`show access-list port 1 detail` では、ポート範囲 (`8000 - 8080`)、プロトコル番号 (`47`)、
末尾の `implicit_deny` (`source-address 0.0.0.0/0; deny`) を含め、Cisco の ACL とルール単位で一致しました。
エントリ数は 5 (変換した 4 エントリ + implicit_deny) で、TCAM へのコンパイルも成功しています。

補足: 検証時点では `deny ip any any` (r40) が条件なしの空マッチで出力されていました。
現在のツールは `implicit_deny` と同じ `source-address 0.0.0.0/0` 条件を付けて出力します
(ARP など IP 以外の通信を遮断しないため)。この構文自体は `implicit_deny` として実機で受理済みですが、
変更後の r40 そのものの再投入は行っていません。

### 5.4 通常ミラーリング (検証項目 2、3)

`.xsf` がそのまま生成する次のコマンド列を投入し、`show mirror` で全フィルタ種別
(ingress only / egress only / ingress and egress / VLAN ソース) が正しく反映されることを確認しました。

```
create vlan "VLAN_20" tag 20
configure vlan Default delete ports 12
create mirror monitor_1
configure mirror monitor_1 to port 12
configure mirror monitor_1 add port 1 ingress
configure mirror monitor_1 add port 2 egress
configure mirror monitor_1 add port 3 ingress-and-egress
configure mirror monitor_1 add vlan "VLAN_20"
enable mirror monitor_1
```

```
show mirror
    monitor_1 (Enabled)
        Mirror to port: 12
        Port 1, all vlans, ingress only
        Port 2, all vlans, egress only
        Port 3, all vlans, ingress and egress
        All ports, vlan VLAN_20, ingress only
    HW mirror instances used: 1 ingress, 1 egress (Maximum 4 total, 1 egress)
```

スタック構成 (機器 ②) でも、スロット 2 のモニターポート (`2:12`) に対しスロット 1 のポート
(`1:1` 〜 `1:3`) を ingress / egress / both で登録し、逆方向 (モニター `1:11`、ソース `2:1`) でも
同様に確認しました。いずれもエラー・確認プロンプトなしで受理されました。

あわせて確認した挙動:

- **`enable mirror` の対話プロンプト**: モニターポートが `Default` に残ったまま `enable mirror` を
  実行すると `Warning: This command will remove VLAN membership from the monitor port. Do you want to
  continue? (y/N)` が表示され、`load script` が止まります。ツールが先に
  `configure vlan Default delete ports <モニターポート>` を出力しているのはこのためです。
- **`DefaultMirror` の再利用**: `create mirror` なしで `configure mirror DefaultMirror to port ...` と
  `enable mirror DefaultMirror` が動作します (マッピングで `DefaultMirror` を指定した場合の経路)。
- **同時有効化の上限**: X440-G2 では最大 4 インスタンス、うち egress 方向のフィルタを持てるのは 1 つのみ。
  2 つ目の egress を追加すると `Error: Maximum number of egress mirrors (1) already enabled!` で拒否されます。
  スタック構成でも同じ上限がスタック全体に適用されます (モニターが別スロットでも共有)。

### 5.5 FSPAN (検証項目 4)

ツールが生成した `monitor_1_filter.pol` をバイト単位そのままアップロードし、
`check policy` で構文検証したうえで、`.xsf` の生成順どおりに投入しました。

```
check policy monitor_1_filter                 -> Policy file check successful.
configure vlan Default delete ports 12
create mirror monitor_1
configure mirror monitor_1 to port 12
configure access-list monitor_1_filter ports 1 ingress
configure access-list monitor_1_filter ports 1 egress
configure access-list monitor_1_filter ports 2 ingress
configure access-list monitor_1_filter ports 2 egress
enable mirror monitor_1
```

4 バインドすべて成功し、`enable mirror` 時の確認プロンプトも発生しませんでした。
Cisco の `deny` に相当するエントリは `permit` のみ (mirror なし) で出力され、通信を止めないことを確認しています。

**バインド順序の制約 (重要)**: 上記の順序を入れ替え、`enable mirror` を先に実行してから
egress 方向の ACL を適用すると、次のエラーで失敗します (ingress は同条件でも成功)。

```
configure access-list monitor_1_filter ports 1 egress
Error: ACL install operation failed - vlan *, port 1, rule "r10", Feature unavailable
```

`mirror;` (既定)、`mirror DefaultMirror;`、作成直後で未有効化のインスタンスを参照する ACL は
いずれも egress バインドに成功し、失敗するのは「有効化済みインスタンスへの参照」の場合のみでした。
命名方式には依存しません。本ツールは ACL 適用 → `enable mirror` の順で生成することでこれを回避しています
(両方向でこの順序が安全なため、方向を区別せず常に適用)。

### 5.6 whole-port egress フォールバック (検証項目 5)

egress 方向の ACL ミラーリングに対応しない機種 (4220 シリーズなど) 向けの代替設定
(`mirror_egress_mode: "whole-port"`) を、X440-G2 上で検証しました。

```
configure vlan Default delete ports 12
create mirror monitor_1
configure mirror monitor_1 to port 12
configure mirror monitor_1 add port 1 egress
configure mirror monitor_1 add port 2 egress
configure access-list monitor_1_filter ports 1 ingress
configure access-list monitor_1_filter ports 2 ingress
enable mirror monitor_1
```

```
show mirror
    monitor_1 (Enabled)
        Mirror to port: 12
        Port 1, all vlans, egress only
        Port 2, all vlans, egress only
show access-list
        1  monitor_1_filter  ingress
        2  monitor_1_filter  ingress
```

エラー・確認プロンプトなし。ingress = ACL、egress = ポート全体の共存を同一インスタンスで確認しました。

### 5.7 実機検証の結論

本ツールが生成する ACL、通常ミラーリング、FSPAN、whole-port egress フォールバックの各設定は、
スタンドアロン・スタックの両構成で実機に投入し、エラーなく受理・反映されることを確認しました。
5.4 〜 5.5 で判明した制約 (モニターポートの事前削除、バインド順序、egress 上限) は
ツールの生成ロジックに反映済みで、利用者側での追加対応は不要です。

一方、egress ACL ミラーリングの対応可否と同時有効化数の上限は機種依存のため、
導入先機種の仕様確認と、実機投入前の検証環境での確認を推奨します。
本検証は上記 5 項目に限定されており、大規模 VLAN 構成や他機能との組み合わせまでを網羅するものではありません。

## 6. 今回の整理で修正した点 (回帰テスト対象)

コードの整理にあわせて次の不具合・改善を実施し、それぞれテストを追加しています。

| # | 内容 | 修正後の挙動 | 対応テスト |
|---|---|---|---|
| 1 | `deny ip any any` (および `permit ip any any`) が条件なしの空マッチで出力され、EXOS では ARP など IP 以外の通信も対象になっていた | `implicit_deny` と同じ `source-address 0.0.0.0/0` 条件を付与 | `AclTests.test_policy_file_and_port_apply`, `AclListCfgTests` |
| 2 | ポートを文字列順に並べるため `Gi1/0/10` が `Gi1/0/2` より前に出力され、LAG のマスターも最小番号にならなかった (`grouping 10,9`) | 自然順 (数値順) に統一。マスターは最小番号のメンバー | `InterfaceSortKeyTests`, `PortTests.test_ports_in_natural_order`, `LagTests`, `DemoCfgTests` |
| 3 | `vlan internal allocation policy ...` が VLAN ブロックと誤認され、余分な警告が出ていた | グローバル行として扱い、未対応行にのみ記録 | `test_vlan_global_commands_are_not_blocks` |
| 4 | `end` が未対応行として報告されていた | 消費するだけ (報告しない) | `GlobalTests.test_end_and_ip_routing_are_consumed`, `DemoCfgTests` |
| 5 | ネイティブ VLAN の未定義チェックがなかった | 警告を出す (タグ 1 は除外) | `VlanReferenceTests.test_undefined_access_trunk_and_native_vlans` |
| 6 | LAG メンバーポート自身の `description` が出力されなかった | メンバーの description を出力 (Port-channel の description はマスターで優先) | `LagTests.test_lacp_bundle_config_on_master`, `StackDemoCfgTests` |
| 7 | `Tw` (TwoGigabitEthernet)、`Fi` (FiveGigabitEthernet) の省略形が展開されなかった | 展開される | `CanonicalizeInterfaceNameTests.test_abbreviations` |
| 8 | `main.py` がパーサ内部の関数を直接呼んでおり、`pyproject.toml` の入口も存在しないモジュールを指していた | 解析の入口を `parser.parse_cisco_config()` に、CLI を `cli.py` に集約。`pip install -e .` で `cisco-exos-translate` が使える | `CliTests` 全体 |

## 7. テストを追加するときの指針

- 新しい Cisco コマンドに対応したら、`tests/test_parser.py` に最小限の設定スニペットで 1 テスト、
  `tests/test_generator.py` に期待する EXOS 出力行のテストを 1 つ追加する。
- 名前の正規化や範囲表記の追加は `tests/test_helpers.py` に追加する。
- 警告文を変更したら、該当テストの期待文字列と `docs/usage.md` の警告一覧を更新する。
- 同梱サンプル設定を変えると `tests/test_samples.py` の期待値が変わるため、
  新しい境界条件はサンプルではなくスニペットテストで表現する。
- 実機で新たな制約が見つかったら、ジェネレータの出力順序に反映し、本書 5 章に追記する。
