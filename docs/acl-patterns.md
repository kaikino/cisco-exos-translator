# Cisco ACL パターン整理 (EXOS Policy File 変換のスコープ定義)

Cisco IOS/IOS-XE の ACL を EXOS のポリシーファイル (`.pol` + `configure
access-list <name> ports ... ingress`) へ変換するにあたり、一般的な Cisco ACL
のパターンを整理し、本ツールの対応範囲を定義するドキュメント。SE レビュー用。
下表の ◯ は現在の実装 (`parser.py` の ACE 解釈と `generator.py` のポリシー生成) と
一致しており、[software-testing.md](software-testing.md) の `AclTests` /
`AclListCfgTests` と [hardware-testing.md](hardware-testing.md) 4 章で検証済み。
(マッチ条件・アクションの表現力は Dynamic ACL と同一のため、本表は出力方式に
かかわらず有効)

**記号の見方:**

- ◯ = 自動変換する (実装済み)
- △ = 変換せず、警告として報告する(手動移行。将来対応の候補)
- ✕ = 対象外(警告として報告のみ)

方針は本ツールの既存の設計と同じ: 変換できるものは正確に変換し、
できないものは**必ず警告に残す**(黙って消さない)。

---

## 1. ACL 種別 (ACL types)

| 種別 | Cisco 構文例 | 頻度 | 対応 |
|---|---|---|---|
| Standard numbered | `access-list 10 permit 10.1.0.0 0.0.0.255` | common | ◯ |
| Extended numbered | `access-list 100 permit tcp any host 10.2.0.5 eq 22` | common | ◯ |
| Standard named | `ip access-list standard MGMT` | common | ◯ |
| Extended named | `ip access-list extended SERVERS-IN` | common | ◯ |
| IPv6 | `ipv6 access-list V6-IN` | occasional | △ EXOS側も対応可能だが現在はIPv4のみ |
| MAC ACL | `mac access-list extended ...` | rare | △ |
| VACL | `vlan access-map` + `vlan filter` | rare | △ 適用機構が異なる |
| Reflexive / dynamic (lock-and-key) | `evaluate` / `dynamic` | rare | ✕ ステートフル動作はEXOS ACLに等価物なし |
| time-range 付き | `permit ... time-range WORKHOURS` | rare | ✕ |

- numbered の範囲: standard 1–99, 1300–1999 / extended 100–199, 2000–2699

## 2. ACE マッチパターン (match patterns)

Extended ACE の構成要素ごと。EXOS 欄は `.pol` の entry 内に記述する条件
(Dynamic ACL の conditions 部と同一構文)。

| 要素 | Cisco 構文例 | 頻度 | EXOS (.pol 条件) | 対応 |
|---|---|---|---|---|
| アクション | `permit` / `deny` | common | `"permit"` / `"deny"` | ◯ |
| プロトコル | `ip` | common | (protocol 条件なし = 全IP) | ◯ |
| | `tcp` / `udp` | common | `protocol tcp` / `protocol udp` | ◯ |
| | `icmp` | common | `protocol icmp` | ◯ |
| | プロトコル番号 (`esp`, `gre`, `47`…) | occasional | `protocol <番号>` | ◯ |
| 送信元/宛先 | `any` | common | (条件なし) | ◯ |
| | `host 10.2.0.5` | common | `source-address 10.2.0.5/32` | ◯ |
| | `10.1.0.0 0.0.0.255` (連続 wildcard) | common | `source-address 10.1.0.0/24` | ◯ |
| | 不連続 wildcard (`10.1.0.0 0.0.255.0`) | rare | **CIDR で表現不可** | ✕ |
| ポート | `eq 22` (数値) | common | `destination-port 22` (送信元側は `source-port`) | ◯ |
| | `range 1200 1250` | common | `destination-port 1200 - 1250` | ◯ |
| | `eq www` / `eq domain` など名前付きポート | common | 数値への変換表が未実装 | △ |
| | `gt` / `lt` / `neq` | occasional | range に展開可能だが要精査 | △ |
| TCP 状態 | `established` | occasional | 等価物なし(TCP flags で部分近似のみ) | △ |
| ログ | `log` / `log-input` | occasional | 要検証(action-modifier) | △ |
| ICMP タイプ | `echo`, `echo-reply` 等 | occasional | 要検証 | △ |
| QoS マーキング条件 | `dscp ef` / `precedence 5` | occasional | 条件としては存在するが未実装 | △ |
| フラグメント | `fragments` | rare | 要検証 | △ |
| シーケンス番号 | `10 permit ...` | common | 適用順序として消費(番号自体は不要) | ◯ |
| コメント | `remark Block guest to servers` | common | 生成コンフィグ内コメント化 | ◯ |
| object-group | `object-group network SRV` + 参照 | occasional | 展開して複数ルール化(ルール数増に注意) | △ |

## 3. 適用コンテキスト (どこで ACL が使われているか)

| コンテキスト | Cisco 構文例 | 頻度 | 対応 |
|---|---|---|---|
| インターフェース in | `ip access-group SERVERS-IN in` | common | ◯ `configure access-list <name> ports <p> ingress` (.pol を事前アップロード) |
| Port-channel in | `interface Port-channelN` + `ip access-group X in` | occasional | ◯ LAG のマスターポートに `ports <master> ingress` で適用 |
| SVI (Router ACL) | `interface VlanN` + `ip access-group X in` | common | ◯ `configure access-list <name> vlan "<VLAN名>" ingress` ※EXOSはVLAN内ブリッジ通信もフィルタするためCiscoより厳しくなる(警告を出力) |
| SPAN フィルタ (FSPAN) | `monitor session N filter ip access-group X` | occasional | ◯ `permit` エントリに `mirror <名前>;` を付けた `<ミラー名>_filter.pol` をソースポートに適用。ACL は通信を止めない(詳細は [usage.md](usage.md) 8.6 節) |
| インターフェース out | `ip access-group X out` | occasional | △ EXOS egress は制約あり(空マッチ不可、機種依存) |
| VTY | `access-class MGMT in` | common | △ EXOS では別機構(SSH/telnet アクセス制御) |
| SNMP | `snmp-server community X RO MGMT` | common | △ 別機構 |
| route-map / NAT / QoS class-map | `match ip address ...` 等 | occasional | ✕ L2変換の対象外(該当ACLは警告で列挙) |

**注**: △/✕ のコンテキストでも「その ACL がどこで参照されているか」は
警告として必ず出力する(参照ごと黙って消えることはない)。対象外の場所でしか
参照されていない ACL は `ACL X: defined but not applied to any translated target; skipped`、
SPAN 宛先ポートやルーテッドポート上の `ip access-group` も同様に警告となる。

## 4. 意味差の注意点 (semantic gaps) — 変換時に自動処理するもの

1. **implicit deny**: Cisco の ACL は末尾に暗黙の deny all を持つが、
   **EXOS はマッチしないパケットをデフォルト permit する**。
   → 変換時に `implicit_deny` エントリを各 ACL の末尾に自動追加する。
   このエントリと `permit / deny ip any any` は、条件なしの空マッチにすると
   ARP など IP 以外の通信まで対象になるため、`source-address 0.0.0.0/0`
   (IPv4 全体) の条件付きで出力する(Cisco の IP ACL は IP 以外に触れない)。
2. **評価順序**: Cisco は ACE を上から順に評価。EXOS の `.pol` もファイル内の
   entry 順で評価されるため、ジェネレータは ACE 順に entry を出力する。
3. **命名**: ポリシー名 = `.pol` ファイル名(ACL 名を EXOS 命名規則に
   サニタイズ。例: `SERVERS-IN` → `SERVERS_IN`、番号 ACL `100` → `V_100`)。
   entry 名は `r10, r20, ...`(Cisco のシーケンス番号は引き継がず振り直す)。
   `remark` は `#` コメントとして `.pol` 内に保持される(スイッチ上に残る)。
4. **部分変換はしない**: 1 つの ACE に対応外のトークンが 1 つでもあれば、
   その行ごと「Not translated」に回す(`permit tcp any any established` を
   `permit tcp any any` に丸めることはしない。deny を落とすと過剰許可になるため)。
   対応外の ACE を含む ACL には `ACL X: N rule(s) could not be translated; the
   generated ACL is incomplete` の警告を出し、適用前の確認を促す。

## 5. 対応範囲まとめ (SE 確認用)

**自動変換 (実装済み):**

- standard / extended の IPv4 ACL(numbered・named とも)
- permit / deny、protocol `ip`/`tcp`/`udp`/`icmp`/番号指定
- `any` / `host` / 連続 wildcard mask、数値の `eq` / `range` ポート指定(送信元・宛先とも)
- `remark`、シーケンス番号、物理ポート / Port-channel / SVI への `in` 適用、
  SPAN フィルタ (FSPAN) としての参照
- 末尾への `implicit_deny` 自動追加(implicit deny の再現、IPv4 限定マッチ)

**警告として報告(手動移行):**

- `out` 適用、`established`、`log`、名前付きポート (`eq www`)、ICMP タイプ、
  `gt`/`lt`/`neq`、object-group、VTY / SNMP 等の別コンテキスト参照、IPv6、MAC ACL

**対応済み(注意つき): SVI への Router ACL** — VLAN への ingress 適用として
変換される。ただし EXOS は VLAN 内のブリッジ通信もフィルタするため Cisco の
Router ACL より厳しくなる(変換時に警告を出力)。

**対象外:**

- 不連続 wildcard mask、reflexive / time-range、route-map / NAT / QoS 参照

---

**確認のお願い**: 上記の頻度・対応範囲は一般的な構成を想定した仮置きです。
実際の移行対象に近い ACL コンフィグ例(サニタイズ済みで可)をいただければ、
頻度欄と対応範囲を実態に合わせて調整します。
