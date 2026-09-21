# weekly-maintenance

Homebrewの更新候補とMoleのクリーンアップ候補を毎週月曜8:30に確認し、処理ごとにGUIで明示承認を得てから実行するLaunchAgent用スクリプトです。自動起動の登録は本リポジトリには含まれません。

## Macでの受入確認・登録

1. このリポジトリを `~/Library/Mobile Documents/com~apple~CloudDocs/Dev/scripts/launchd/weekly-maintenance` にcloneし、plistに記載した絶対パスとの一致を確認します。
2. `command -v brew; brew --version; command -v mo; mo --version; mo clean --help; mo clean --dry-run` を実機で調べます。Moleの削除対象表示、ドライラン後の対象再走査、GUIセッションと非対話実行時の最終確認を実証するまではLaunchAgentを登録・有効化しません。実削除を伴う確認は安全な検証環境で行います。
3. `python3 -m unittest discover -s tests -p 'test_*.py'` を実行し、実機でGUIの承認／拒否／無応答、実行ログ、月曜8:30の起動とスリープ復帰時の起動を確認します。模擬テストだけで実機検証済みとは扱いません。
4. 検証・受入後に `mkdir -p "$HOME/Library/LaunchAgents"` を実行し、`launchd/com.hnishim.weekly-maintenance.plist` を `"$HOME/Library/LaunchAgents/"` へ配置し、`launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.hnishim.weekly-maintenance.plist"` で有効化します。既存の同名Agentがある場合は重複登録せず状態を確認します。

Homebrewは表示したパッケージ名だけを`brew upgrade`へ渡します。Moleは実行時に対象を再走査するため、プレビューと実削除の対象一致は保証されません。承認時に全対象を表示できない場合は実削除しません。`brew autoremove` は `mo clean` と重複するため、`mo installer` は追加の便益が未実証のため、週次処理には含めていません。通常の出力・エラーは `~/Library/Logs/weekly-maintenance.log` で確認できます。
