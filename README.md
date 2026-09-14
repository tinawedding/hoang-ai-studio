# HN AI VOICE STUDIO PRO

Ứng dụng web tiếng Việt, giao diện đen vàng, ưu tiên **tải video → xử lý âm thanh → xuất MP4 thật**.

## Có trong bản 0.1

- Upload trực tiếp từng phần lên máy chủ; không chờ trình duyệt đọc metadata gốc.
- Nhận MP4/MOV/MKV/WEBM/AVI/M4V bằng kiểm tra nội dung file và codec trên máy chủ.
- Tạo bản xem trước H.264/AAC tương thích trình duyệt; hỗ trợ tua bằng HTTP Range.
- Chỉnh cao độ từ -12 đến +12 semitone, giữ thời lượng; giảm nhiễu, EQ 3 dải, lọc tiếng ù, nén động, cân bằng âm lượng, fade.
- Xử lý video gốc, giữ kích thước (làm tròn chẵn khi cần), xuất H.264 CRF 18 / AAC 192 kbps.
- Hàng đợi một tiến trình, tiến độ từ FFmpeg, hủy xử lý, lỗi có thông báo.
- A/B bản gốc và bản đã xử lý; tải MP4 chỉ khi có kết quả.
- File riêng theo cookie phiên ký số; mật khẩu truy cập chung cấu hình trên máy chủ.
- Xóa file thủ công; tự xóa sau 2 giờ; tối đa 4 video/phiên, 250 MB/video, 15 phút/video.

## Phạm vi và giới hạn

**Đây là đổi tông và xử lý âm thanh DSP, chưa phải mô hình AI chuyển danh tính giọng.** Hiệu ứng tác động lên cả lời nói và nhạc có sẵn. Chưa tách giọng khỏi nhạc, clone giọng, đăng nhập Google, quản trị người dùng, lưu trữ lâu dài hoặc thanh toán.

Bản 0.1 chỉ lưu tạm trên ổ máy chủ. Render Free có hệ thống file tạm, file có thể mất khi máy chủ khởi động lại. Không quảng cáo đây là lưu trữ cloud bền vững. Ứng dụng không ghi video vào localStorage/IndexedDB, không tự tải xuống máy người dùng. Trình duyệt vẫn dùng RAM/bộ đệm để phát. Muốn lưu dự án lâu dài cần thêm object storage và database, rồi kiểm thử riêng.

Video được mã hóa lại, không phải xuất lossless. Ngôn ngữ/giai điệu và màu giọng tự nhiên có thể bị ảnh hưởng khi đổi nhiều semitone. Preview giảm độ phân giải; xuất dùng video gốc. Giới hạn 4K/60 fps giúp bảo vệ tài nguyên, không phải cam kết Render Free xử lý 4K nhanh.

File yêu cầu "Pasted markdown(2).md" trong cuộc trò chuyện chưa đọc được trong phiên triển khai ban đầu. Bản này thực hiện luồng ưu tiên đã nêu trực tiếp; cần đối chiếu nội dung file trước khi coi toàn bộ sản phẩm là hoàn tất.

## Chạy

Python 3.11+:

~~~bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
~~~

FFmpeg được cung cấp bởi imageio-ffmpeg; metadata được đọc bằng PyAV. Không cần cài FFmpeg từ một URL tải nhị phân không xác minh.

Chỉ dùng **một Uvicorn worker / một instance** trong bản này: hàng đợi và quyền sở hữu tác vụ nằm trong RAM. Không autoscale trước khi chuyển state sang Redis/database.

Biến môi trường:

| Biến | Ý nghĩa |
| --- | --- |
| APP_ACCESS_KEY | Mật khẩu chung của phòng thu; không đưa vào Git |
| REQUIRE_ACCESS_KEY | true khi triển khai; mật khẩu tối thiểu 12 ký tự |
| SESSION_SECRET | Chuỗi ngẫu nhiên ký cookie; không đưa vào Git |
| PUBLIC_ORIGIN | Origin HTTPS công khai nếu reverse proxy không chuyển đúng host/protocol |
| DATA_DIR | Thư mục tạm riêng của ứng dụng, mặc định /tmp/hn-voice-studio |
| MAX_UPLOAD_MB | 250 mặc định |
| MAX_SECONDS | 900 mặc định |
| RETENTION_SECONDS | 7200 mặc định |
| PROCESS_TIMEOUT | 1800 mặc định |

Nếu không đặt APP_ACCESS_KEY, chế độ phát triển không yêu cầu mật khẩu. Không dùng chế độ này cho dịch vụ công khai.

## Render

Có render.yaml cho một Python web service tại Singapore, plan free. **Chưa tạo dịch vụ chỉ vì đã có file cấu hình.** Cần xác nhận workspace Render trước khi triển khai qua connector. APP_ACCESS_KEY nhập tại Dashboard; SESSION_SECRET tự sinh. Không đặt mật khẩu trong source code hoặc build log.

Sau triển khai cần kiểm tra /healthz, tải video bằng trình duyệt, nghe A/B và tải MP4 tại URL thật. Test CI thành công không thay thế kiểm thử trên Render.

## Kiểm thử

~~~bash
pip install -r requirements-dev.txt
python -m pytest tests/test_pipeline.py -v
python -m playwright install chromium
python -m pytest tests/test_browser.py -v
~~~

Tests tạo video có âm thanh thật bằng FFmpeg, gửi qua API, đợi xử lý, giải mã kết quả và kiểm tra codec, thời lượng, cao độ và đồng bộ. Kiểm tra thêm file hỏng, video im lặng, quyền truy cập, giới hạn upload, tải theo Range, hủy và xóa. Kiểm thử trình duyệt bao gồm giao diện desktop/mobile và toàn bộ thao tác upload/render/download.

## Tài liệu tham chiếu

- [FFmpeg audio filters](https://ffmpeg.org/ffmpeg-filters.html)
- [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg)
- [PyAV](https://pyav.org/docs/stable/)
- [Render Free và giới hạn lưu trữ](https://render.com/docs/free)
