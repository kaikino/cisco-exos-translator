# Cisco → Extreme EXOS コンフィグ変換ツール 使い方マニュアル

## 1. 本ツールの位置づけ

本ツールは、Cisco Catalyst スイッチ (IOS / IOS-XE) の `running-config` テキストを読み込み、
Extreme EXOS (Switch Engine) 向けの設定スクリプト (`.xsf`) とポリシーファイル (`.pol`) を
自動生成するコマンドラインツールです。

- 変換は **1 台ずつ独立** に行います。複数スイッチ間の VLAN 整合性やトポロジの相関は扱いません。
- 生成結果は「たたき台」です。`.xsf` 冒頭の WARNINGS を確認したうえで適用してください。
- 変換できない設定行は **黙って捨てず**、必ず WARNINGS に一覧化します。

対応範囲の概要:

| 領域 | 内容 |
|---|---|
| レイヤー 2 | VLAN 定義、アクセス / トランクポート、ネイティブ VLAN、リンクアグリゲーション (LACP / 静的)、ポートの description と shutdown |
| スタック | メンバー番号によるポート表記 (`slot:port`)、優先度、スタック構築手順書の生成 |
| ACL | IPv4 の standard / extended ACL (番号付き・名前付き) と `ip access-group ... in` によるポート / SVI への適用 |
| 基本的な L3 | SVI の IPv4 アドレス、スタティックルート |
| ミラーリング | ローカル SPAN (`monitor session`)、ACL フィルタ付き SPAN (FSPAN) |

スパニングツリー、QoS、ポート速度 / デュプレックス、PoE、音声 VLAN、ルーテッド物理ポート、
ルーティングプロトコル、RSPAN / ERSPAN などは対象外です (詳細は 9 章)。

## 2. 動作要件とインストール

| 項目 | 内容 |
|---|---|
| Python | 3.9 以上 (検証環境: 3.9.6) |
| 追加パッケージ | 不要 (標準ライブラリのみ) |
| 入力 | `show running-config` の出力テキスト (UTF-8) |

インストールは不要です。リポジトリ直下で `python3 main.py` を実行できます。
コマンドとして導入したい場合は次を実行すると `cisco-exos-translate` コマンドが使えるようになります。

```bash
pip install -e .
```

## 3. コマンドの使い方

### 3.1 書式

```
python3 main.py <cisco_config.cfg> [<cisco_config2.cfg> ...]
```

引数は変換したい Cisco 設定ファイルのパスです。複数指定するとそれぞれ独立に変換します。
オプションはありません。変換内容の調整はすべてマッピングファイル (6 章) で行います。

### 3.2 実行例

1 台分を変換する:

```bash
python3 main.py configs/sw-floor1.cfg
```

複数台をまとめて変換する:

```bash
python3 main.py configs/*.cfg
```

`pip install -e .` 済みの場合:

```bash
cisco-exos-translate configs/sw-floor1.cfg
```

同梱のサンプル設定で動作確認する:

```bash
python3 main.py sample.cfg
```

### 3.3 画面出力と終了コード

処理の進行は標準出力、警告件数の要約は標準エラー出力に書き出されます。
警告の本文は画面には出ず、`.xsf` の冒頭に埋め込まれます。

```
sample.map.json written — edit it to customize, then re-run
sample.cfg -> sample.xsf
sample.cfg -> sample-acls/ (1 .pol file(s); upload to the switch before loading the .xsf)
sample.cfg -> sample.stack-setup.txt (run before loading the .xsf)
  17 warning(s) — see the WARNINGS header in sample.xsf
```

| 終了コード | 状況 |
|---|---|
| 0 | 正常終了 (警告があっても 0) |
| 1 | 引数なし (書式を表示)、設定ファイルが読めない、マッピングファイルが JSON として不正 |

入力ファイルはすべて先に読み込んでから書き出しを始めるため、
1 つでも読めないファイルがあれば何も生成せずに終了します。

## 4. 出力ファイル

入力ファイル `<名前>.cfg` と同じディレクトリに、次のファイルが生成されます。

| ファイル | 内容 | 生成条件 |
|---|---|---|
| `<名前>.map.json` | 変換の対応関係 (VLAN 名、ポート番号、LAG のマスターとモード、ミラー名など)。**初回実行時のみ** 生成され、以後は上書きされません | 常に |
| `<名前>.xsf` | EXOS に読み込む設定スクリプト本体。冒頭に WARNINGS と変換対応表を `#` コメントで含みます。**実行のたびに再生成** されます | 常に |
| `<名前>-acls/*.pol` | ACL と FSPAN フィルタ用のポリシーファイル。1 ACL につき 1 ファイル。`.xsf` の読み込み前にスイッチへアップロードが必要です | 適用済みの ACL または FSPAN がある場合 |
| `<名前>.stack-setup.txt` | スタック構築手順書。EXOS のスタック化はモード変更と再起動を伴うため `.xsf` には含められず、手順書として分離しています | スタックメンバーが 2 台以上の場合 |

## 5. 基本的な作業手順

```bash
python3 main.py sw1.cfg      # ① 初回実行: sw1.map.json と sw1.xsf を生成
vi sw1.map.json              # ② 対応関係を確認・編集
python3 main.py sw1.cfg      # ③ 再実行: 編集内容を反映した sw1.xsf を再生成
```

1. **初回実行**。マッピングファイルと `.xsf` が生成されます。
2. **マッピングファイルの確認・編集** (6 章)。特にアップリンクポートの番号 (`uplinks.start`) は
   移行先機種に合わせて必ず設定してください。未設定のままだと `.xsf` に `{uplink-m1-p1}` のような
   プレースホルダが残り、そのままでは読み込めません (誤って適用しないための意図的な仕様です)。
3. **再実行**。マッピングの編集内容を反映した `.xsf` が再生成されます。
4. **WARNINGS の確認** (7 章)。「Not translated」に挙がった設定は手動で移植が必要です。
5. **スタック構成の場合**: `.stack-setup.txt` の手順でスタックを構築してから次へ進みます。
6. **`.pol` のアップロード** (存在する場合)。`.xsf` より **先に** スイッチへ置きます。

   ```
   tftp get <TFTPサーバIP> vr VR-Mgmt SERVERS_IN.pol
   check policy SERVERS_IN
   ```

7. **`.xsf` の読み込みと確認**。

   ```
   tftp get <TFTPサーバIP> vr VR-Mgmt sw1.xsf
   load script sw1.xsf
   show vlan / show sharing / show access-list / show mirror
   save configuration
   ```

## 6. マッピングファイル (.map.json) の編集

マッピングファイルは「Cisco の設定から自動で導いた変換上の判断」を JSON で書き出したものです。
値を書き換えて再実行すると、その値が優先されます。

```json
{
  "vlans":  { "10": "USERS", "20": "VLAN_20" },
  "ports":  { "GigabitEthernet1/0/1": "1:1", "TenGigabitEthernet1/1/1": "1:{uplink-m1-p1}" },
  "uplinks": { "start": null },
  "lags":   { "Port-channel1": { "master": "1:10", "mode": "lacp" } },
  "mirrors": { "1": "monitor_1" },
  "mirror_egress_mode": { "1": "acl" }
}
```

| セクション | キー → 値 | 説明 |
|---|---|---|
| `vlans` | Cisco の VLAN タグ → EXOS の VLAN 名 | 名前は EXOS の命名規則 (英字始まり、英数字と `_`、32 文字以内) に整形済みです。タグ 1 は組み込みの `Default` に固定されます |
| `ports` | Cisco のインターフェース名 → EXOS のポート番号 | スタック構成なら `slot:port`、単体なら `port`。個別に書き換えられます |
| `uplinks.start` | 移行先スイッチの最初のアップリンクポート番号 | 例: 48 ポート機なら `49`。設定すると `{uplink-mN-pP}` プレースホルダがすべて `start + P - 1` に置き換わります。`ports` に個別指定があればそちらが優先されます |
| `lags` | Port-channel 名 → `master` と `mode` | `master` はメンバーポートのいずれか。`mode` は `lacp` または `static` |
| `mirrors` | SPAN セッション番号 → EXOS のミラーインスタンス名 | `DefaultMirror` を指定すると組み込みインスタンスを再利用し、`create mirror` を省略します |
| `mirror_egress_mode` | SPAN セッション番号 → `acl` または `whole-port` | フィルタ付き SPAN のみ対象。`whole-port` にすると egress 側はポート全体をミラーします (8.6 節) |

編集時のルール:

- 現在の Cisco 設定に存在しないインターフェースの項目は無視されます (警告あり)。
- Cisco 設定に後から増えたインターフェースは自動導出値で補われます (警告あり)。
- `lags` は項目単位で上書きできます。`mode` だけ書いても `master` は自動導出値が使われます。
- `_help` セクションは説明用で、削除しても構いません。

## 7. 生成される .xsf の読み方

`.xsf` は上から順に「WARNINGS」「Translation reference (変換対応表)」「設定本体」で構成されます。

### 7.1 WARNINGS の 3 分類

| 分類 | 意味 |
|---|---|
| **Input** | Cisco 設定自体の不整合。例: 定義されていない VLAN をポートが参照している |
| **Not translated** | 本ツールの対象外だった Cisco 設定行。コマンド文字列ごとにまとめ、出現回数と行番号を付けて一覧化します。**手動移植が必要な箇所** です |
| **Translation** | 変換時に本ツールが行った判断。例: 許可 VLAN 未指定のトランクを全 VLAN に展開した、ミラー名が重複したので改名した |

### 7.2 Translation reference

アクティブなマッピングの内容 (VLAN 名、ポート番号、LAG、ミラー) を `#` コメントで示します。
変更したい場合はマッピングファイルを編集して再実行します。

### 7.3 設定本体の構成

```
# System                     configure snmp sysName
# Stacking                   (確認用コメント)
# VLANs                      create vlan
# Link aggregation           enable sharing
# Port configuration         description / VLAN 所属 / disable ports
# L3                         configure vlan ipaddress / configure iproute add
# ACLs                       configure access-list ... ingress
# Mirroring (SPAN)           create mirror / configure mirror / enable mirror
```

## 8. 変換仕様

### 8.1 対応する Cisco コマンドと EXOS 出力

| Cisco IOS / IOS-XE | EXOS 出力 |
|---|---|
| `hostname X` | `configure snmp sysName "X"` |
| `vlan 10` + `name USERS` | `create vlan "USERS" tag 10` |
| `vlan 20,30` / `vlan 40-42` (リスト・範囲) | VLAN ごとに `create vlan` |
| `switchport access vlan 10` | `configure vlan Default delete ports <p>` の後に `add ports <p> untagged` |
| `switchport mode trunk` + `trunk allowed vlan ...` | 許可 VLAN ごとに `add ports <p> tagged` |
| `switchport mode trunk` (許可リストなし) | 定義済みの全 VLAN (Default を除く) に `tagged` で展開 (警告) |
| `switchport trunk native vlan 10` | ネイティブ VLAN を `untagged` で追加 (tagged の対象から除外) |
| `switchport trunk allowed vlan add / remove ...` | 許可集合に加算 / 減算 |
| `channel-group N mode active / passive` | `enable sharing <master> grouping <members> algorithm address-based L3_L4 lacp` |
| `channel-group N mode on` | 静的 sharing (`lacp` なし) |
| `interface Port-channelN` の L2 設定 | LAG のマスターポートに適用 |
| `interface range Gi1/0/2-4` | 展開して各ポートに適用 |
| `shutdown` | `disable ports <p>` |
| `description X` | `configure ports <p> description-string "X"` |
| スタック表記 (`Gi1/0/1`, `Gi2/0/24`) | スタック時は `slot:port`、単体時は `port` |
| IPv4 ACL + `ip access-group <ACL> in` | ACL ごとに `.pol` を生成し、`configure access-list <ACL> ports <p> ingress` で適用 |
| `interface VlanN` + `ip address A MASK` | `configure vlan "<名前>" ipaddress A/len` + `enable ipforwarding vlan "<名前>"` |
| `ip route P MASK GW` | `configure iproute add P/len GW` (0.0.0.0/0 は `default`) |
| `monitor session N source interface ... [rx / tx / both]` + `destination interface X` | セッションごとに `create mirror` / `configure mirror ... to port` / `add port <p> ingress / egress / ingress-and-egress` / `enable mirror` |
| `monitor session N source vlan ...` | `configure mirror <名前> add vlan "<名前>"` (EXOS は ingress のみ。`tx` / `both` は警告) |
| `monitor session N filter ip access-group <ACL>` (FSPAN) | `permit` エントリに `mirror <名前>;` を付けたポリシーファイルをソースポートに適用 |

対応するインターフェース種別と省略形:

| 正式名 | 省略形 |
|---|---|
| FastEthernet | Fa |
| GigabitEthernet | Gi |
| TwoGigabitEthernet | Tw |
| FiveGigabitEthernet | Fi |
| TenGigabitEthernet | Te |
| TwentyFiveGigE | Twe |
| FortyGigabitEthernet | Fo |
| HundredGigE | Hu |
| Port-channel | Po、`port-channel 1` (空白あり) |

### 8.2 VLAN と Default VLAN

- VLAN 名は EXOS の規則に整形します。名前のない VLAN は `VLAN_<タグ>`、名前が重複する場合は
  タグを末尾に付けて区別します (例: `GUEST`, `GUEST_41`)。
- タグ 1 は EXOS 組み込みの `Default` VLAN に対応づけ、作り直しません。
  Cisco 側で VLAN 1 に別名が付いていても無視します (警告)。
- EXOS のポートは初期状態で `Default` に untagged 所属しています。そのため他の VLAN へ untagged で
  追加する前に `configure vlan Default delete ports <p>` を出力します。
- ポートから参照されているのに定義されていない VLAN は、`.xsf` が有効になるよう自動作成します (警告)。

### 8.3 リンクアグリゲーション

- マスターポートは最も番号の小さいメンバー (自然順。`Gi1/0/9` は `Gi1/0/10` より前) です。
  マッピングファイルで変更できます。
- Port-channel の VLAN 設定はマスターポートにのみ出力します。メンバーポート自身の `description` と
  `shutdown` は保持されます。
- PAgP (`auto` / `desirable`) は EXOS に等価物がないため LACP として出力します (警告)。

### 8.4 スタック

- スタックメンバーが 2 台以上のとき、ポートは `slot:port` 表記になります。
- `switch N provision <機種>` の Cisco 機種名は EXOS のスロット種別に対応づけられないため、
  確認用コメントとして出力します。優先度 (`switch N priority`) は手順書に反映されます。
- 生成される `.stack-setup.txt` は、各ノードでの `enable stacking-support` と再起動、
  メンバー 1 だった機器での `enable stacking` (Easy Setup)、優先度設定、`.xsf` 読み込みまでを
  段階的に記述しています。

### 8.5 ミラーリング (SPAN)

- 1 セッション = 1 ミラーインスタンス。既定名は `monitor_<セッション番号>` です。
- 方向は rx → `ingress`、tx → `egress`、both → `ingress-and-egress` に対応します。
- Cisco の SPAN 宛先ポートは通常のトラフィックを流さないため、EXOS 側のモニターポートは
  全 VLAN から外し、ミラー専用にします。元設定の VLAN / ACL / shutdown は適用しません (警告)。
  `description` のみ引き継ぎます。
- ソースが Port-channel の場合はメンバーポートに展開します。宛先が Port-channel または
  LAG メンバーの場合、EXOS ではモニターポートを load-share グループに入れられないため、
  そのセッションはスキップします (警告)。
- EXOS のミラーは常に VLAN タグを保持します (Cisco の `encapsulation replicate` 相当)。
  この指定がないセッションは差異として警告します。
- `configure vlan Default delete ports <モニターポート>` を `enable mirror` より先に出力します。
  これがないと `enable mirror` が対話プロンプト (y/N) を出し、`load script` が止まります。
- 同時に有効化できるミラー数には機種ごとの上限があります (X440-G2 実測: 4、うち egress を持てるのは 1)。
  複数セッションを変換した場合は警告します。実機での確認結果は
  [hardware-testing.md](hardware-testing.md) を参照してください。

### 8.6 フィルタ付きミラーリング (FSPAN)

`monitor session N filter ip access-group <ACL>` は、ソースポート全体ではなく ACL で
`permit` された通信だけをミラーする設定です。次のように変換します。

- `<ミラー名>_filter.pol` を生成します。すべてのエントリのアクションは `permit` (通信を止めない) で、
  Cisco の `permit` エントリにだけ `mirror <ミラー名>;` を付けます。Cisco の `deny` は
  「ミラーしない」という意味なので、`permit` のみ (mirror なし) で出力します。
- ミラーインスタンスの `add port` の代わりに、`configure access-list <名前> ports <p> ingress / egress`
  でソースポートに適用します。
- ACL の適用は **`enable mirror` より前** に出力します。実機検証で、有効化済みインスタンスを参照する
  ACL を egress 方向に適用すると `Feature unavailable` で失敗することが分かっているためです
  ([hardware-testing.md](hardware-testing.md) 7 章)。
- VLAN ソースにはフィルタを適用できません (フィルタなしでミラーし、警告)。
- **`mirror_egress_mode`**: 4220 シリーズなど egress 方向の ACL ミラーアクションに対応しない機種では、
  マッピングファイルで該当セッションを `"whole-port"` にしてください。egress 側は
  `configure mirror <名前> add port <p> egress` (ポート全体、フィルタなし) になり、
  ingress 側は引き続き ACL で絞り込まれます。

### 8.7 ACL

- 対応する構文: `permit` / `deny`、プロトコル `ip` / `tcp` / `udp` / `icmp` / 番号、
  `any` / `host` / 連続ワイルドカードマスク、数値の `eq` / `range`、`remark`、シーケンス番号、
  インターフェースへの `in` 適用。
- ポリシー名 = `.pol` のファイル名 (ACL 名を EXOS 命名規則に整形。例: `SERVERS-IN` → `SERVERS_IN`、
  番号 ACL `100` → `V_100`)。エントリ名は `r10, r20, ...` で、`remark` は `#` コメントとして残します。
- Cisco の ACL 末尾には暗黙の deny がありますが、EXOS はマッチしない通信を許可します。
  そのため各 `.pol` の末尾に `implicit_deny` エントリを追加します。このエントリと
  `permit / deny ip any any` は、ARP など IP 以外の通信まで遮断しないよう
  `source-address 0.0.0.0/0` (IPv4 全体) の条件付きで出力します。
- SVI への適用 (Router ACL) は `configure access-list <ACL> vlan "<名前>" ingress` に変換します。
  EXOS は VLAN 内のブリッジ通信もフィルタするため、Cisco より厳しくなります (警告)。
- 1 行でも変換できない ACE を含む ACL は「不完全」として警告します。
  対応範囲の詳細は [acl-patterns.md](acl-patterns.md) を参照してください。

## 9. 対象外・制限事項

- **L3**: ルーテッド物理ポート (`no switchport` + `ip address`)、`secondary` アドレス、
  IP 以外のネクストホップ (`Null0` など)、VRF、ルーティングプロトコルは対象外です。
- **アップリンクポートの番号**: 移行先機種のポート配置が必要なため、`uplinks.start` または
  `ports` の個別指定で解決してください。
- **ACL**: `established`、`log`、名前付きポート (`eq www`)、`gt` / `lt` / `neq`、不連続ワイルドカード、
  object-group、`out` 方向、VTY / SNMP / route-map での参照は変換せず、警告として報告します。
- **SPAN**: RSPAN (`remote vlan`)、ERSPAN (`type ...`)、`filter ip access-group` 以外のフィルタは
  警告として報告します。ソースまたは宛先のないセッションはスキップします。
- スパニングツリー、ポート速度 / デュプレックス、PoE、QoS、storm-control、音声 VLAN、
  LACP タイマーは対象外です。
- 複数スイッチにまたがる VLAN の統合や分散 LAG の相関は行いません。

## 10. 主な警告メッセージと対処

| 警告 (英語原文の要点) | 意味と対処 |
|---|---|
| `... access VLAN N is referenced but not defined` | 未定義 VLAN を参照。出力では自動作成されるので、意図どおりか確認 |
| `... trunk allowed VLAN N / trunk native VLAN N is referenced but not defined` | 同上 (トランク) |
| `... routed interface ... is outside L2 conversion scope` | L3 ポート。手動で移植 |
| `... interface has access mode but trunk VLAN settings` (逆も同様) | Cisco 側の設定矛盾。どちらが正しいか確認 |
| `... trunk has no allowed-VLAN list ...; expanded to all N non-Default VLANs` | 許可リストなしのトランクを全 VLAN に展開。不要な VLAN があれば `.xsf` を調整 |
| `... unresolved uplink placeholder '{uplink-m1-p1}'` | `uplinks.start` を設定して再実行 |
| `EXOS port 'N' is the target of multiple Cisco interfaces` | マッピングでポート番号が重複。修正必須 |
| `Port-channelN: PAgP mode(s) ... defaulting to LACP` | 対向機器も LACP に変更するか、`mode` を `static` に |
| `Stack member N: Cisco model ... cannot be mapped to an EXOS slot type` | スロット種別は EXOS 機器側で設定 |
| `ACL X: N rule(s) could not be translated; the generated ACL is incomplete` | 変換されなかった ACE (Not translated に列挙) を手動で追加 |
| `ACL X: defined but not applied to any translated target; skipped` | 対象外の場所 (VTY、SNMP など) でのみ参照されている ACL。必要なら手動移植 |
| `... ACL X applied to vlan ... is stricter than the Cisco router ACL` | EXOS は VLAN 内通信もフィルタ。通信要件を確認 |
| `monitor session N: EXOS mirroring preserves VLAN tags on the copies` | キャプチャ側でタグ付きフレームを扱えるか確認 |
| `monitor session N: monitor port(s) ... removed from all VLANs` | モニターポートがミラー専用になる。想定どおりか確認 |
| `monitor session N: egress ACL mirror action is platform-dependent` | 移行先機種の対応可否を確認し、必要なら `mirror_egress_mode` を `whole-port` に |
| `N mirror instances translated: EXOS platforms limit concurrently enabled mirrors` | 機種の上限を確認 |
| `mapping: unknown ... entry 'X' ignored` / `no entry for ... using the derived default` | マッピングファイルと Cisco 設定の不一致。必要なら再生成 |

## 11. Python から使う

CLI を使わず、他のスクリプトから直接呼び出すこともできます。

```python
from cisco_exos_translator import (
    parse_cisco_config,       # 文字列 -> ParsedConfig (中間表現 + 警告)
    build_default_mapping,    # ParsedConfig -> マッピング dict
    generate_exos_config,     # (ParsedConfig, mapping) -> (.xsf 文字列, 警告, {.pol 名: 内容})
    generate_stack_setup,     # (ParsedConfig, xsf ファイル名) -> 手順書文字列 or None
)

config = parse_cisco_config(open("sw1.cfg", encoding="utf-8").read())
mapping = build_default_mapping(config)
mapping["uplinks"]["start"] = 49

xsf, warnings, pol_files = generate_exos_config(config, mapping)
print(xsf)
for name, text in pol_files.items():
    open(f"{name}.pol", "w").write(text)
```

中間表現の構造は [architecture.md](architecture.md) を参照してください。
