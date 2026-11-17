# AI VIDEO STUDIO

Hệ thống sản xuất video AI hàng loạt kết hợp **Flow2API** (`https://flow.aetrading.vn/v1` — Nano Banana 2.1 + Omni 1.2 / Veo 3.1), **Continuity Engine** (khóa Master Image & truyền tham chiếu liên cảnh), **8-Layer Dual Prompt Builder**, **PostgreSQL**, **Redis + Dramatiq**, và **FFmpeg Video Composer** (ghép cảnh, đồng bộ thời lượng thực tế, chèn phụ đề `.srt`, nhạc nền, dọn file tạm tự động).

---

## 1. Khởi động nhanh trên máy hiện tại (One-command Setup)

Môi trường local đã được cài đặt sẵn PostgreSQL 18 (`ai_video_studio`, 17 bảng nghiệp vụ), Redis, FFmpeg/FFprobe và Python `.venv`.

### Bật toàn bộ dịch vụ nền (PostgreSQL + Redis + FastAPI + Dramatiq Worker)
```bash
./start-local.sh
```
Kiểm tra trạng thái hoặc tắt dịch vụ nền:
```bash
./start-local.sh status
./start-local.sh stop
```
- **API & Swagger UI**: http://127.0.0.1:8010/docs
- **Health Check**: http://127.0.0.1:8010/api/v1/health

---

## 2. Chạy bằng lệnh CLI (`./studio`)

### 2.1 Kiểm tra sức khỏe hệ thống & danh sách model
```bash
./studio doctor
./studio models
```

### 2.2 Chạy thử End-to-End Offline (không cần API key)
Tự sinh nhân vật + background mẫu, tạo kịch bản 3 cảnh + bộ đôi Prompt 8 lớp, sinh 3 clip dọc thử nghiệm và ghép bằng FFmpeg ra MP4 `1080x1920` (H.264/AAC) có burn phụ đề `.srt`:
```bash
./studio demo-local --output ./demo_xauusd.mp4
```

### 2.3 Cấu hình Flow2API Key (và LLM viết kịch bản tùy chọn)
Bạn có thể điền trực tiếp vào file `.env` (`FLOW2API_API_KEY=...`) **hoặc** lưu mã hóa vào PostgreSQL bằng lệnh:
```bash
./studio config set --flow2api-key "YOUR_FLOW2API_KEY"
./studio config show
./studio test-api
./studio models --refresh
```
*(Nếu chưa cấu hình `SCRIPT_LLM_API_KEY`, hệ thống tự dùng bộ chia cảnh & template kịch bản thông minh theo thời lượng clip; khi cần dùng OpenAI/Gemini để viết thoại, chạy `./studio config set --llm-provider openai_compatible --llm-base-url https://api.openai.com/v1 --llm-model gpt-4o-mini --llm-key "sk-..."`)*

---

## 3. Sản xuất video thật với Flow2API bằng CLI

### Cách 1: Chạy trọn gói 1 lệnh (`run-pipeline`)
```bash
# 1. Thêm nhân vật & ảnh nền tham chiếu (file ảnh trên máy hoặc URL)
./studio character add --name "Senior Forex Expert" --image /duong/dan/expert.jpg --voice "Mature American male"
./studio background add --name "Trading Office" --image /duong/dan/office.jpg

# 2. Tạo dự án 24 giây (3 cảnh x 8s với Omni 1.2 Portrait)
./studio project create \
  --name "Forex XAUUSD Education" \
  --topic "Smart Money Concepts Liquidity Sweep" \
  --duration 24 \
  --aspect "9:16" \
  --char-id "Senior Forex Expert" \
  --bg-id "Trading Office" \
  --image-model "gemini-3.1-flash-image-portrait-2k" \
  --video-model "omni-1.2-flash-8s-portrait"

# 3. Chạy toàn bộ quy trình: Script -> Master Image -> Continuity Images -> Omni Videos -> FFmpeg MP4
./studio run-pipeline "Forex XAUUSD Education" --auto-approve --output ./final_video.mp4
```

### Cách 2: Chạy từng bước có kiểm duyệt Master Image (Continuity Gate)
```bash
# Bước 1: Sinh kịch bản + bộ đôi Prompt A (Image) & Prompt B (Video) 8 lớp
./studio script generate "Forex XAUUSD Education"
./studio scene list "Forex XAUUSD Education"

# Bước 2: Xem hoặc chỉnh lời thoại / khóa cảnh
./studio scene prompts <SCENE_01_ID>
./studio scene edit <SCENE_01_ID> --dialogue "Gold just swept Asian highs—watch the displacement candle next." --auto-fit

# Bước 3: Tạo ảnh tĩnh Scene 01 (Master Reference Image) và duyệt
./studio scene gen-image <SCENE_01_ID>
./studio scene approve <SCENE_01_ID>

# Bước 4: Tạo ảnh các cảnh tiếp theo (tự động nhận Master Image + ảnh cảnh liền trước để giữ đồng nhất)
./studio scene gen-image <SCENE_02_ID>
./studio scene approve <SCENE_02_ID>

# Bước 5: Tạo video có lời thoại bằng Omni 1.2 / Veo 3.1 cho từng cảnh đã duyệt
./studio scene gen-video <SCENE_01_ID>
./studio scene gen-video <SCENE_02_ID>

# Bước 6: Ghép thành video hoàn chỉnh bằng FFmpeg
./studio render "Forex XAUUSD Education" --transition crossfade --output ./master_render.mp4
```

---

## 4. Kiểm thử tự động (Automated Tests)

```bash
cd backend
uv run pytest -v
```
Bộ test kiểm tra:
- Bộ chia cảnh theo lịch xuất hiện nhân vật (`0-24s`, `24-40s`, `40-60s`) & ước lượng/cắt lời thoại vừa độ dài clip model (`8s`, `6s`, `15s`).
- Bộ dựng 2 Prompt 8 lớp độc lập, cơ chế khóa lớp (`locked_layers`), và retry khi dính `PUBLIC_ERROR_AUDIO_FILTERED`.
- Parser SSE/HTML `<video src='...'>` của Flow2API và phân loại mã lỗi upstream.
- Atomic job claiming, Master Image Continuity Gate và version rollback trên PostgreSQL.
- FFmpeg Video Composer (`concat` + `xfade`/`acrossfade` + `.srt` subtitle burn-in + dọn thư mục tạm).
- Giao diện lệnh CLI (`doctor`, `models`, `project create`, `script generate`, `scene prompts`, `demo-local`).

---

## 5. Triển khai VPS gọn nhẹ (Docker Compose)

Thiết kế tối ưu cho VPS nhỏ (~58 GB ổ cứng, không tải model AI nội bộ):
- Giới hạn `WORKER_VIDEO_CONCURRENCY=1`, `FFMPEG_THREADS=2`.
- Tự động dọn `/tmp/ai-video-studio` sau mỗi job và chặn job mới nếu dung lượng trống xuống dưới `MIN_FREE_DISK_GB`.

```bash
cp .env.example .env
# Điền FLOW2API_API_KEY, mật khẩu DB/JWT và cấu hình R2/Cloudinary nếu dùng trên VPS
docker compose -f deployment/docker-compose.yml up -d --build
```

---

## 6. Trạng thái dự án hiện tại
- **Hoàn tất & đã kiểm thử chạy thật**: CLI (`./studio`), toàn bộ Backend FastAPI (52 API operations), PostgreSQL schema 17 bảng + Alembic migration, Dramatiq Redis worker, Continuity Engine, 8-Layer Prompt Builder, Flow2API client, Storage adapters (Local / Cloudinary / Cloudflare R2), và FFmpeg Video Composer.
- **Đang chờ khi cần mở rộng**:
  1. Điền `FLOW2API_API_KEY` thật để gọi live tới `https://flow.aetrading.vn/v1`.
  2. Xây dựng các màn hình giao diện Next.js (`frontend/app/...`) khi bạn muốn chuyển từ CLI sang giao diện Web Studio.
