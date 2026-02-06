#!/bin/bash
# 臨時檔案：用於繞過 git commit 的 --trailer 參數問題
# 此檔案可隨時刪除，不影響專案運作
cd /home/kapraspi/rasporuno
git add -A
git commit -m "Fix word segmentation spacing and improve furigana display event binding"
