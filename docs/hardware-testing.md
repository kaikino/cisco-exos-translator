# ハードウェアテスト文書 (実機検証)

本ツールが生成した EXOS 設定 (`.xsf` の行と `.pol` ファイル) を Extreme 実機に投入し、
構文の受理、ハードウェアへの反映、機種固有の制約を確認した記録です。
自動テストについては [software-testing.md](software-testing.md) を参照してください。

データポートにケーブルを接続できない環境だったため、トラフィックを流しての確認は行っていません。
本書の検証はすべて **設定レベル** (コマンドの受理、TCAM へのコンパイル、`show` 出力の一致) です。

## 1. 検証環境

| 項目 | 内容 |
|---|---|
| 機器 ① | Extreme Networks X440G2-12p-10G4 (スタンドアロン)、EXOS 33.4.1.15 |
| 機器 ② | X440G2 × 2 台 SummitStack (リング型、スロット 1 = Master、スロット 2 = Backup) |
| 接続 | 各機器の管理ポートへ telnet (管理は VR-Mgmt 上の out-of-band 経路で、データポートの設定変更で管理アクセスが切れることはない) |
| 対象ポート | 各機器とも、他の通信に使用されていない未使用のデータポートのみ |
| ファイル転送 | TFTP サーバがなかったため、スイッチ上の `edit policy` (vi) を expect スクリプトで駆動して `.pol` の内容をそのまま書き込んだ。ファイルは `/usr/local/cfg` に置かれ、`tftp get` と機能的に同じ結果 |

## 2. 検証方針・安全対策

貸出元が管理する共有の検証機器を使用したため、既存の通信・設定に影響を与えないよう次の方針で実施しました。

- 未使用のデータポートのみ使用し、既存の通信が流れているポートや VLAN には手を加えない。
- 設定変更は検証終了時に必ずすべて元に戻し (ロールバック)、`show ports <範囲> vlan` で
  **ポート 1 つずつ** の VLAN 所属を検証前のスナップショットと突合してから終了する。
- `save configuration` は一切実行しない (再起動すれば検証前の状態に戻る)。

## 3. 検証結果一覧

| No  | 検証項目 | 機器 | 結果 |
|---|---|---|---|
| 1 | ACL: 生成した `.pol` (名前付き extended、番号付き extended) の `check policy` とポートへの適用 | ① | ○ |
| 2 | 通常ミラーリング: 複数ポート、rx / tx / both、VLAN ソースを含む構成 | ① | ○ |
| 3 | 通常ミラーリング: スロットをまたぐモニター / ソース配置 (双方向) | ② | ○ |
| 4 | FSPAN: 生成した `.pol` をそのままアップロードして適用 | ① | ○ |
| 5 | whole-port egress フォールバック: ingress = ACL、egress = ポート全体を同一インスタンスで適用 | ① | ○ |

## 4. 検証項目 1: ACL (.pol ポリシーファイル)

機器 ①。スイッチは `pre-dem.cfg` 稼働状態。

### 4.1 生成 (作業用 PC)

```bash
python3 main.py docs/cisco-acl-list.cfg
# -> cisco-acl-list.xsf
# -> cisco-acl-list-acls/SERVERS_IN.pol  (名前付き extended ACL。remark、シーケンス番号つき)
# -> cisco-acl-list-acls/V_100.pol       (番号付き extended ACL)
```

### 4.2 スイッチへの転送

TFTP サーバがある場合の通常手順:

```
tftp get <server-ip> vr VR-Mgmt SERVERS_IN.pol
tftp get <server-ip> vr VR-Mgmt V_100.pol
```

今回は `edit policy <name>.pol` (vi) 経由で同じ内容を書き込みました (1 章参照)。

### 4.3 構文検証と適用

```
check policy SERVERS_IN                          -> Policy file check successful.
check policy V_100                               -> Policy file check successful.
configure access-list SERVERS_IN ports 1 ingress -> done!
configure access-list V_100 ports 2 ingress      -> done!
```

### 4.4 確認

```
show access-list
    Port 1  SERVERS_IN  ingress  Rules: 5
    Port 2  V_100       ingress  Rules: 5
    (5 = 変換した 4 エントリ + implicit_deny。Cisco 側のルール数と一致)

show access-list port 1 detail
    entry r10: protocol tcp; destination-address 10.2.0.5/32; destination-port 22; permit
    entry r20: protocol tcp; source-address 10.1.0.0/24;
               destination-address 10.2.0.5/32; destination-port 8000 - 8080; permit
    entry r30: protocol 47; permit
    entry r40: deny (ip any any)
    entry implicit_deny: source-address 0.0.0.0/0; deny
```

ポート範囲 (`8000 - 8080`)、プロトコル番号 (`47`)、末尾の `implicit_deny` を含め、
Cisco の ACL とルール単位で一致し、TCAM へのコンパイルも成功しました。

補足: 検証時点では `deny ip any any` (r40) が条件なしの空マッチで出力されていました。
現在のツールは `implicit_deny` と同じ `source-address 0.0.0.0/0` 条件を付けて出力します
(ARP など IP 以外の通信を遮断しないため)。この構文自体は `implicit_deny` として実機で受理済みですが、
変更後の r40 そのものの再投入は行っていません。

### 4.5 クリーンアップ (保存なし)

```
unconfigure access-list SERVERS_IN     -> done!
unconfigure access-list V_100          -> done!
show access-list                       -> No entry found!
rm SERVERS_IN.pol                      (y で確認)
rm V_100.pol                           (y で確認)
exit                                   ("save configuration?" に N)
```

## 5. 検証項目 2: 通常ミラーリング (スタンドアロン)

機器 ①。

### 5.1 初期状態

```
show mirror
    DefaultMirror (Disabled) — "Default Mirror Instance, created automatically"
    Mirrors enabled: 0 (Maximum 4)
    HW mirror instances used: 0 ingress, 0 egress (Maximum 4 total, 1 egress)
```

ジェネレータの前提 2 点が確認できました。`DefaultMirror` は常に存在する
(マッピングで `DefaultMirror` を指定した場合に `create mirror` を省略できる) こと、
および同時有効化できるミラー数がハードウェア上限であることです。

### 5.2 投入 (`.xsf` がそのまま生成する構成)

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

すべての行がエラーなし、**対話プロンプトなし** で受理されました。

### 5.3 確認

```
show mirror
    monitor_1 (Enabled)
        Mirror to port: 12
        Port 1, all vlans, ingress only
        Port 2, all vlans, egress only
        Port 3, all vlans, ingress and egress
        All ports, vlan VLAN_20, ingress only
    HW mirror instances used: 1 ingress, 1 egress
```

Cisco 側の rx / tx / both / source vlan とフィルタ単位で一致しました。

### 5.4 あわせて確認した挙動

- **`enable mirror` の対話プロンプト**: モニターポートが `Default` に残ったままの 2 つ目のミラーを
  有効化すると `Warning: This command will remove VLAN membership from the monitor port. Do you want to
  continue? (y/N)` が表示されました。`monitor_1` でプロンプトが出なかったのは、生成された
  `configure vlan Default delete ports 12` が先に実行されていたためです。対話プロンプトは
  `load script` を止めるため、ツールがこの行を必ず出力する根拠になっています。
- **同時有効化**: 3 つのミラー (`monitor_1`、別名のインスタンス、`DefaultMirror`) を同時に有効化でき ました。
- **egress の上限**: 有効化済みの 2 つ目のミラーに egress フィルタを追加すると
  `Error: Maximum number of egress mirrors (1) already enabled!` で拒否されました。
  X440-G2 では egress / both フィルタを持てる有効ミラーは **1 つだけ** です
  (ツールの複数セッション警告はこの制約を反映しています)。
- **`DefaultMirror` の再利用** (マッピング `"mirrors": {"1": "DefaultMirror"}` の経路):
  `configure mirror DefaultMirror to port ...`、`add port ... ingress`、`enable mirror DefaultMirror` が
  `create` なしで動作しました。

### 5.5 クリーンアップ (保存なし)

```
disable mirror monitor_1 / m2 / DefaultMirror
delete mirror monitor_1 / m2          (DefaultMirror は削除不可)
configure mirror DefaultMirror delete port 6
configure mirror DefaultMirror to port none
configure vlan Default add ports 10,11,12 untagged
delete vlan VLAN_20
show mirror / show vlan               -> 初期状態に復帰
```

## 6. 検証項目 3: 通常ミラーリング (SummitStack、`slot:port` 表記)

機器 ② (スロット 1 が Master、以前の SW-STACK-DEMO 変換結果を稼働中)。
ソースは `1:1` 〜 `1:3` (USERS に untagged 所属)、モニターポートは `1:11` / `2:12`
(`Default` のみ所属。ツールが想定するモニターポートの状態と同じ)。
検証前に取得した `show ports ... vlan` のスナップショットと突合してロールバックしました。

- **スロットをまたぐミラーリングは双方向で動作**: モニターがスロット 2、ソースがスロット 1
  (`configure mirror monitor_1 to port 2:12` + `add port 1:1 ingress` / `1:2 egress` /
  `1:3 ingress-and-egress` / `add vlan "USERS"`)、およびその逆 (モニター `1:11`、ソース `2:1 ingress`)。
  生成された行はすべてそのまま受理され、`show mirror` はフィルタ単位で一致しました。
- **対話プロンプトなし**: スタンドアロンと同様、生成された `configure vlan Default delete ports <モニターポート>`
  が先に実行されるためです。
- **上限はスロット単位ではなくスタック単位**: モニターが別スロットにあっても、有効ミラー `Maximum 4` と
  2 つ目の egress フィルタに対する `Error: Maximum number of egress mirrors (1) already enabled!` は同じでした。
- ソース VLAN に、メンバーポートを持つ既存 VLAN (USERS) を指定しても、その VLAN の設定に副作用はありませんでした。

## 7. 検証項目 4: FSPAN (フィルタ付きミラーリング)

機器 ①。元スライドの「DNS サーバとの ICMP だけをミラーする」パターン
(`monitor session N filter ip access-group <ACL>` + 2 行の ACL) を、`sample.cfg` の `MIRROR_PING` /
monitor session 2 に相当する単一セッションの設定 (`fspan-test.cfg`) で検証しました。
以下のミラー名・ポリシー名はその設定のものです。

### 7.1 ポリシーファイル

ツールが生成した `.pol` を、スイッチの `edit policy` (vi) でバイト単位そのままアップロードしました
(動的 ACL による近似ではなく、実出力ファイルそのものを使用)。

```
# monitor_2_filter.pol (生成内容をそのままアップロード)
entry r10 { if { protocol icmp; source-address 100.86.255.2/32; }
            then { permit; mirror monitor_2; } }
entry r20 { if { protocol icmp; destination-address 100.86.255.2/32; }
            then { permit; mirror monitor_2; } }
```

```
check policy monitor_2_filter        -> Policy file check successful.
```

### 7.2 投入 (`.xsf` の生成順)

```
configure vlan Default delete ports 12
create mirror monitor_1
configure mirror monitor_1 to port 12
configure access-list monitor_1_filter ports 1 ingress
configure access-list monitor_1_filter ports 1 egress
configure access-list monitor_1_filter ports 2 ingress
configure access-list monitor_1_filter ports 2 egress
enable mirror monitor_1
```

- **4 バインド (ingress + egress × 2 ポート) すべて成功**し、`enable mirror` の確認プロンプトも発生しませんでした。
- Cisco の `deny` に相当するエントリ (より大きな `fspan-test.cfg` の ACL 末尾にある `10.9.0.0/16` のルール) は
  `permit;` のみ (mirror アクションなし) で出力され、通信を止めないことを確認しました。Cisco FSPAN の意味と一致します。

### 7.3 バインド順序の制約 (重要、ハードウェアが強制)

上記の順序を入れ替え、`enable mirror` を先に実行してから egress 方向の ACL を適用すると、
次のエラーで失敗しました (ingress は同条件でも成功)。

```
configure access-list monitor_1_filter ports 1 egress
Error: ACL install operation failed - vlan *, port 1, rule "r10", Feature unavailable
```

命名方式が原因ではないことを個別に切り分けました (すべて egress バインド):

| ACL のアクション | 参照先の状態 | 結果 |
|---|---|---|
| `permit; mirror;` | DefaultMirror、未有効化 | 成功 |
| `permit; mirror DefaultMirror;` | 未有効化 | 成功 |
| `permit; mirror m6;` | 作成直後、未有効化 | 成功 |
| `permit; mirror m6;` | m6 を有効化した後 | **失敗 (Feature unavailable)** |

失敗するのは「**有効化済み** インスタンスへの参照」を egress 方向にバインドした場合のみです。
本ツールは ACL 適用 → `enable mirror` の順で生成することでこれを回避しています
(両方向でこの順序が安全なため、方向を区別せず常に適用。[generator.py](../cisco_exos_translator/generator.py))。
修正後の順序で再投入したところ、4 バインドすべてエラーなし・プロンプトなしで成功しました。

### 7.4 クリーンアップで見つかった問題と教訓

先行の切り分け (`enable mirror m6`、ポート 11) の後、そのミラーを削除した際にポート 11 の VLAN 所属を戻し忘れ、
ポート 11 が `Default` から外れたままになっていました。`show vlan` のポート **数** では気づかず、
`show ports ... vlan` のポート単位の一覧で `0/15` と `0/16` の差として検出し、セッション終了前に復旧しました。

今後の実機セッションでは、`show vlan` のポート数ではなく **ポート単位の VLAN 一覧** で初期状態への復帰を確認します
(2 章の方針はこの教訓を反映したものです)。

## 8. 検証項目 5: whole-port egress フォールバック

機器 ①。egress 方向の ACL ミラーアクションに対応しない機種 (4220 シリーズなど) 向けの
代替設定 (マッピング `mirror_egress_mode: "whole-port"`) を検証しました。元スライドの 4220 系エッジスイッチ
2 台で実際に使われていたハイブリッド構成 (ingress は ACL で絞り込み、egress はポート全体を無条件にミラー、
同一インスタンス) と同じパターンです。

```
check policy monitor_1_filter        -> Policy file check successful.
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
        1  monitor_1_filter  ingress  3  0
        2  monitor_1_filter  ingress  3  0
```

すべての行がエラーなし・プロンプトなしで受理されました。`show mirror` は egress がフィルタなし (ポート全体)
であること、`show access-list` は ingress が引き続き ACL で絞り込まれていることを示しています。
16 ポートすべてが `Default` に所属する初期状態へロールバックし、再確認しました。

## 9. 判明した制約とツールへの反映

| 制約 (実機で確認) | ツールの対応 |
|---|---|
| モニターポートが VLAN に所属したまま `enable mirror` すると対話プロンプト (y/N) が出て `load script` が止まる | `configure vlan Default delete ports <モニターポート>` を `enable mirror` より前に出力 |
| 有効化済みインスタンスを参照する `mirror` アクション付き ACL は egress 方向にバインドできない (`Feature unavailable`) | フィルタ ACL の適用を `enable mirror` より前に出力 (方向を問わず常に) |
| X440-G2 では有効ミラー最大 4、うち egress / both フィルタを持てるのは 1 つ。スタックでも上限はスタック全体で共有 | 複数セッションを変換した場合に警告を出力 |
| 4220 シリーズなどは egress の ACL ミラーアクション自体に非対応 | マッピング `mirror_egress_mode: "whole-port"` で egress をポート全体ミラーに切り替え (ingress は ACL のまま) |
| `DefaultMirror` は常に存在し、削除できない | マッピングで `DefaultMirror` を指定した場合は `create mirror` を省略 |

## 10. 結論

本ツールが生成する ACL、通常ミラーリング、FSPAN、whole-port egress フォールバックの各設定は、
スタンドアロン (ポート番号のみ) と 2 台 SummitStack (`slot:port`、スロットをまたぐ配置を含む) の両構成で
実機に投入し、エラーなく受理・反映されることを確認しました。スイッチ側の解釈 (`show` 出力) は
Cisco 側の設定とルール / フィルタ単位で一致しています。9 章の制約はツールの生成ロジックに反映済みで、
利用者側での追加対応は不要です。

一方、egress ACL ミラーリングの対応可否と同時有効化数の上限は機種依存のため、導入先機種の仕様確認と、
実機投入前の検証環境での確認を推奨します。本検証は上記 5 項目に限定されており、
大規模 VLAN 構成や他機能との組み合わせ、トラフィックを流した動作までを網羅するものではありません。
