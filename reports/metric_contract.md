# Metric contract (khai báo bắt buộc — chốt ở phút 30, trước khi chạy eval)

> Các metric chuẩn đã được cài trong `src/metrics/tracks.py`. File này là nơi nhóm **cam kết** cách dùng chúng.
> Không đổi testset, threshold hay trọng số sau khi đã thấy kết quả eval.

| Mục | Khai báo của nhóm |
|---|---|
| Track | T02 · Raw QC Triage |
| Primary metric (tên đúng như evaluator in ra) | `Defect Recall@B` (kèm `Precision@B`) |
| Tăng tốt hay giảm tốt? | higher |
| Đơn vị đánh giá (image / region / pair / release / batch / case …) | image |
| Công thức + numerator / denominator | Recall@B = #ảnh lỗi thật (`is_defect=1`) nằm trong top-B theo `score` / #ảnh lỗi thật trên split eval. Precision@B = cùng numerator / B |
| Cách ghép prediction ↔ GT (IoU? id? tolerance?) | khớp theo `image_id`; xếp theo `score` giảm dần, hoà điểm giữ thứ tự ổn định (mergesort) |
| Candidate pool cố định | ảnh có `split=eval` trong `fixtures/t02_raw_qc/images.csv` (40 ảnh, 10 cảnh gốc) |
| Review budget B / K | B = 20 |
| Seed | 42 |
| Threshold policy (chọn trên dev như thế nào) | Không có nhãn → không tune theo nhãn. Mỗi loại lỗi so với **median của cùng cảnh gốc**, chia cho "1 đơn vị lỗi" cố định chọn bằng cách nhìn pixel/feature trên split `dev` (blur 2.5 log-lap, dark 1.0 log-brightness, glare +0.10 tỉ lệ pixel cháy trong 1 ô, compression +1.0 blockiness); ngưỡng RECAPTURE = 1.0. Baseline: ngưỡng = quantile 0.75 của `-log(lap_var)` trên `dev`. (Bản đầu dùng quantile-0.9 trên dev làm thang → glare bị phóng 25–125×; đã thay **trước khi** chạy eval, ghi trong report.) |
| Eval split (không dùng để tune) | `eval` (tách theo cảnh gốc) |
| Chỉ số lỗi xấu (miss / false alarm / false reject) | miss (ảnh lỗi lọt ra ngoài top-B) xấu nhất; false alarm ảnh đêm/cam_B là lỗi phụ |
| Slices sẽ báo cáo (≥2) | `light` (day/dusk/night), `defect_type` (blur/dark/glare/compression), `camera_id` (cam_A/cam_B) |
| Cách báo độ bất định khi N nhỏ (Wilson CI / bootstrap) | Wilson 95% CI cho Recall@B và Precision@B (N eval = 40) |
| Ngày chốt theo config và người phụ trách | 2026-11-14 · Do Phuong Manh (ManhDo), owner theo `configs/lab.yaml` |

Nếu baseline = 0 thì ghi rõ "baseline = 0" thay vì phần trăm cải thiện.
