#!/bin/bash
# 臨時檔案：用於繞過 git commit 的 --trailer 參數問題
# 此檔案可隨時刪除，不影響專案運作
cd /home/kapraspi/rasporuno
git add -A
git commit -m "Change word click style to match rasrss: remove button appearance, use text node click"
