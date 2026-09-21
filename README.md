# weekly-maintenance

Homebrewの更新候補とMoleの清掃候補を毎週月曜日8:30に確認します。定期処理は**確認・通知・結果保存だけ**です。更新・削除は行いません。

## 実行方法

次の場所へこのリポジトリを配置してください（plistの絶対パスと一致させます）。

```text
~/Library/Mobile Documents/com~apple~CloudDocs/Dev/scripts/launchd/weekly-maintenance
```

```bash
cd "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Dev/scripts/launchd/weekly-maintenance"
bash scripts/weekly-maintenance.sh check
cat "$HOME/Library/Logs/weekly-maintenance/last-check.txt"
```

候補があるときだけmacOS通知を出します。通知を見逃した場合や通知が失敗した場合も、`~/Library/Logs/weekly-maintenance/last-check.txt` に最後の確認結果と手動実行方法が残ります。確認結果ファイルは直近1回分を上書きし、独自の履歴・再試行機構はありません。結果保存場所は環境変数 `WEEKLY_MAINTENANCE_REPORT` で上書きできます。

更新・清掃する場合だけ、利用者が別途ターミナルから次を起動してください。

```bash
bash scripts/weekly-maintenance.sh run
```

手動実行時にHomebrewの情報更新と両ツールの候補確認を再実施し、**HomebrewとMoleを別々に承認**します。Homebrewでは現在表示したパッケージ名だけを `brew upgrade` へ渡します。Moleのドライランは参考情報です。通常清掃 `mo clean` は実行時に再走査するため、通知時・確認時とは削除対象が変わり得ます。Mole全体の清掃に承認した後、Mole自身の対話・権限要求を省略せず実行します。拒否・無応答・確認失敗ではその処理を実行しません。清掃対象を個々のファイル単位で固定・承認する機構ではありません。

## 定期起動の登録・解除（Mac実機での受入確認時）

plistの絶対パスが実際の配置先と一致することを確認してから、登録します。以前の受入失敗候補を登録・実行しないでください。

```bash
mkdir -p "$HOME/Library/LaunchAgents"
cp launchd/com.hnishim.weekly-maintenance.plist "$HOME/Library/LaunchAgents/"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.hnishim.weekly-maintenance.plist"
```

登録後に確認専用モードを実行する場合は次を使います。

```bash
launchctl kickstart -k "gui/$(id -u)/com.hnishim.weekly-maintenance"
cat "$HOME/Library/Logs/weekly-maintenance/last-check.txt"
```

解除するには次を実行します。

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.hnishim.weekly-maintenance.plist"
```

`StartCalendarInterval` は毎週月曜8:30です。`RunAtLoad` は指定せず、スリープ中に予定時刻を過ぎた場合は復帰時の起動に任せます。電源オフ中の取りこぼしは許容します。手動の `run` はLaunchAgentから呼びません。

## テストと受入確認

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
bash -n scripts/weekly-maintenance.sh
plutil -lint launchd/com.hnishim.weekly-maintenance.plist
```

自動テストでは `brew`・`mo`・`osascript` を模擬しており、実データの更新や削除は行いません。Mac実機で、通知センターへの表示、結果保存、承認ダイアログ、Mole通常清掃時の追加確認・権限要求、登録・解除・月曜8:30・スリープ復帰を別途確認してください。通常清掃は実データの削除を伴うため、対象と影響を確認してから明示的に実行してください。
