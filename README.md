# Weekly-maintenance

Homebrewの更新候補とMoleの清掃候補を毎週月曜日8:30に確認します。定期処理は**確認・通知・結果保存だけ**です。更新・削除は行いません。

ソーススクリプトはiCloud Driveで同期し、LaunchAgentが実行するスクリプトは各Macの `~/Library/Application Support/my.launchd.weekly-maintenance` に配置します。`macOS` の実行元制限により、iCloud Drive上のbashスクリプトをLaunchAgentから直接実行できない場合があるためです。

## 実行方法

次の場所へこのリポジトリを配置してください。

```text
~/Library/Mobile Documents/com~apple~CloudDocs/Dev/scripts/launchd/weekly-maintenance
```

```bash
bash "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Dev/dotfiles/launchd/weekly-maintenance-setup.sh"
```

Dotfiles側のセットアップスクリプトが、iCloud Drive上のスクリプトをローカル実行領域へコピーし、Homebrew/Moleを解決できるPATHを設定したplistを生成してLaunchAgentへ登録します。ソースを更新した場合は、同じセットアップスクリプトをもう一度実行してローカル実行コピーを更新してください。同期と登録の処理はdotfiles側に一本化しています。

登録後の確認専用実行と結果確認は次のとおりです。

```bash
launchctl kickstart -k "gui/$(id -u)/my.launchd.weekly-maintenance"
cat "$HOME/Library/Logs/weekly-maintenance/last-check.txt"
```

候補があるときだけ `terminal-notifier` でmacOS通知を出します。**通知をクリックすると** `warp://tab_config/weekly-maintenance` からWarpの新規タブで承認付き `run` を起動します。通知の表示・閉じる操作や定期 `check` 自体は、更新・清掃や承認ダイアログを開始しません。通知権限の状態やWarpが未起動の場合の実際の挙動はMac実機で確認してください。

通知を見逃した場合や通知が失敗した場合も、`~/Library/Logs/weekly-maintenance/last-check.txt` に最後の確認結果と手動実行方法が残ります。通知コマンドが見つからない、または通知送信に失敗した場合は `check` が失敗を報告しますが、保存済みレポートと手動実行経路は残ります。確認結果ファイルは直近1回分を上書きし、独自の履歴・再試行機構はありません。結果保存場所は環境変数 `WEEKLY_MAINTENANCE_REPORT` で上書きできます。

通知を見逃した後も、WarpのTab Configが適用済みなら次のコマンドから同じ承認付き手動実行を開始できます。既存のタブにコマンドを送らず、既存ウィンドウ内の新しいタブを開きます。

```bash
open 'warp://tab_config/weekly-maintenance'
```

初回セットアップ時は、dotfilesの `brew/packages.yml` にある `terminal-notifier` をインストールし、`apps/warp/warp-setup.sh` で `~/.warp/tab_configs/weekly-maintenance.toml` を同期してください。`launchd/weekly-maintenance-setup.sh` も実行し、`~/Library/Application Support/my.launchd.weekly-maintenance/weekly-maintenance.sh` の実行用コピーを最新にしてください。Tab Configを適用できない場合や通知のリンクが開けない場合は、レポートに残る `bash … run` をWarpの対話タブから直接実行できます。

更新・清掃する場合だけ、利用者が別途ターミナルから次を起動してください。

```bash
bash scripts/weekly-maintenance.sh run
```

手動実行時はHomebrewの定義更新・通常候補の再確認後、更新対象と実行範囲を示して承認を求めます。Homebrewの承認範囲は、表示済み通常候補の更新と`brew upgrade --cask --greedy`による追加cask更新です。通常候補が0件でもgreedy cask更新は対象が生じ得るため、承認が必要です。更新対象の全件を事前の通常候補から固定するものではありません。通常更新が失敗した場合はgreedy cask更新を省略します。`brew cleanup`と`brew autoremove`は週次スクリプトから明示的に実行せず、Homebrew標準の自動清掃に任せます。

Homebrewの通常更新・greedy cask更新、Mac App Store全体の`mas upgrade`、インストール済みグローバルnpmパッケージの更新を**1回の包括承認**で実行します。npmは実行時に`npm outdated --global --depth=0 --json`で候補を確認し、グローバルのインストール先と現在・互換範囲・最新版を表示したうえで、候補を`@latest`へ更新します（メジャーバージョン更新を含みます）。専用のpnpm管理textlint実行環境は更新対象に含めません。前提確認に失敗した区分は承認対象から除外し、実行しません。最後にMoleの清掃候補を参考表示して**Moleだけ独立承認**を求めます。Moleの`mo clean`は実行時に再走査し、追加確認・権限要求を省略しません。Cancel・ダイアログ失敗・不明な応答では該当区分の変更処理を実行しません。承認ダイアログには120秒の自動終了期限を設けません。終了時には処理別の成功・失敗・未承認・省略を表示します。

旧`scripts-commands/brew-upgrade.sh`のRaycast起動経路は退役し、更新処理本体はこの`weekly-maintenance.sh`のみです。実行用コピーの更新には上記のdotfilesセットアップを再実行し、旧版を新しいものに入れ替えてください。定期LaunchAgentは引き続き月曜8:30の`check`専用です。

## 定期起動の登録・解除（Mac実機での受入確認時）

以前の受入失敗候補を登録・実行しないでください。登録はdotfiles側のセットアップスクリプトだけで行います。

```bash
bash "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Dev/dotfiles/launchd/weekly-maintenance-setup.sh"
```

登録後に確認専用モードを実行する場合は次を使います。LaunchAgentからはローカル実行領域のコピーが呼ばれるため、iCloud Drive上のbashスクリプトを直接実行しません。

```bash
launchctl kickstart -k "gui/$(id -u)/my.launchd.weekly-maintenance"
cat "$HOME/Library/Logs/weekly-maintenance/last-check.txt"
```

解除するには次を実行します。

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/my.launchd.weekly-maintenance.plist"
```

`StartCalendarInterval` は毎週月曜8:30です。`RunAtLoad` は指定せず、スリープ中に予定時刻を過ぎた場合は復帰時の起動に任せます。電源オフ中の取りこぼしは許容します。手動の `run` はLaunchAgentから呼びません。

## テストと受入確認

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
bash -n scripts/weekly-maintenance.sh
plutil -lint launchd/my.launchd.weekly-maintenance.plist
```

自動テストでは `brew`・`mas`・`npm`・`mo`・`osascript`・`terminal-notifier` を模擬しており、実データの更新や削除は行いません。Mac実機で、通知センターへの表示、結果保存、承認ダイアログ、Mole通常清掃時の追加確認・権限要求、登録・解除・月曜8:30・スリープ復帰を別途確認してください。通常清掃は実データの削除を伴うため、対象と影響を確認してから明示的に実行してください。
