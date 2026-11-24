# Intel Radar // 個人情報雷達 v0.1

純本地 Apple Silicon (`MLX`) 驅動的個人情報守門員：
1. **🚨 世界重大情報（不夠大條絕不吵你）**：併發聚合 18 個全球一線新聞與科技信源（BBC、NYT、CNBC、Guardian、TechMeme、The Verge、Ars Technica、Hacker News 等），只有達到 **Lv.9–10 核彈級劇變** 才主動發通知；Lv.7–8 靜音收錄，日常雜訊直接攔截。
2. **🕵️ 小道消息傳言版（33% / 67% 三欄證據計分制）**：自動從爆料與供應鏈管道（MacRumors、9to5Mac、Wccftech、Tom's Hardware、Insider Gaming 等）收集傳聞，禁止模型亂猜機率，改由**客觀證據指標 + 多方共振**計算可信度，分為：
   - **🟢 可能性極高 (`68% – 100%`)**：硬證據流出（FCC/跑分庫/程式碼）或多方獨立信源交叉印證
   - **🟡 待觀察風聲 (`34% – 67%`)**：具紀錄爆料客或單一供應鏈消息
   - **🔴 不太可信 (`0% – 33%`)**：孤證匿名爆料或誇大臆測
3. **📱 手機端推播預留**：內建 macOS 原生通知，並已接好 **ntfy.sh**、**iOS Bark** 與 **Telegram Bot** 推播介面，在網頁右上角「⚙️ 通知與門檻」填入即可串上手機。

## 一鍵啟動

```bash
cd /Users/joeychen/Documents/Personal/intel-radar
./run.sh
```

啟動後瀏覽器會自動打開戰情室：`http://127.0.0.1:8765`
