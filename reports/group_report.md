# Group Report — Day 06 Data Lab (Field-to-Release PoC)

## 00 · Header

| Mục | Giá trị |
|---|---|
| Group ID | 2A202603148 |
| Challenge | T02 · Raw QC Triage |
| Thành viên và vai trò | Do Phuong Manh · bài cá nhân, phụ trách pipeline, kiểm thử, rà soát và báo cáo |
| Git URL | https://github.com/manhtotbung/K5-L2A-DAY06-DoPhuongManh-2A202603148-DataGovernanceAndPrivacy |
| Tag `demo-180m` (commit) | Mốc đóng gói demo. SHA tra bằng `git rev-parse demo-180m^{commit}`. |
| Tag `final-24h` (commit) | Mốc đóng gói cuối. SHA tra bằng `git rev-parse final-24h^{commit}`. |
| Deadline (giờ VN) | Chưa có giờ cụ thể trong tài liệu đã đối chiếu |

## 01 · Problem statement

- Người phụ trách thu thập ảnh chịu chi phí chụp lại khi hệ thống báo nhầm. Ảnh lỗi bị bỏ sót có thể làm giảm chất lượng dữ liệu ở bước sau.
- Ngân sách rà soát là 20 ảnh mỗi lô. Pipeline xếp hạng ảnh theo dấu hiệu mờ, thiếu sáng, lóa và nén để ưu tiên mẫu cần xem.
- Đầu vào gồm ảnh và metadata. Đầu ra gồm `ranked_review.csv`, hàng đợi rà soát và quyết định lô. Mỗi ảnh có hành động `KEEP` hoặc `RECAPTURE`.
- Theo `src/pipeline.py` và `configs/lab.yaml`, lô ở trạng thái HOLD khi hàng đợi chưa được duyệt đủ. Sau khi duyệt đủ, tỷ lệ RECAPTURE vượt 30% dẫn đến REWORK. Nếu tỷ lệ không vượt ngưỡng, GO chỉ áp dụng cho ảnh KEEP.
- Giả định các biến thể cùng cảnh có cùng nội dung nền để so sánh đặc trưng. Người phụ trách dữ liệu cần xác nhận tiêu chí sử dụng ảnh trước khi áp dụng cho dữ liệu thật.

## 02 · Data & provenance

| Split | N | Nguồn / link | License | Synthetic? |
|---|---:|---|---|---|
| dev | 56 | [Fixture T02](../fixtures/t02_raw_qc/images.csv) | CC0-1.0 | Có |
| eval / heldout | 40 | [Fixture T02](../fixtures/t02_raw_qc/images.csv) | CC0-1.0 | Có |

- `images.csv` có các cột `image_id`, `path`, `camera_id`, `light`, `split`. Các lớp lỗi theo đề T02 gồm `none`, `blur`, `glare`, `dark`, `compression`.
- Seed 42. Split theo cảnh gốc. Số ảnh lấy từ `images.csv`. Nguồn synthetic và license lấy từ [manifest](../fixtures/t02_raw_qc/manifest.csv).
- `python -m tools.verify_manifests` xác minh SHA-256 thành công, gồm manifest của T02. Lượt chạy sử dụng fixture synthetic.

## 03 · Research & design

- Đề T02 hướng dẫn dùng phương sai Laplacian làm baseline và phân tích ảnh đêm, lóa cục bộ, quang học cam_B. Metric contract xác định cách so sánh cùng ngân sách trên tập eval. Xem mục 11.
- Luồng dữ liệu gồm kiểm hash và đọc ảnh → trích đặc trưng → tính điểm theo cảnh → lấy top-B → áp dụng quyết định rà soát và ghi audit → quyết định lô.
- PoC dùng xử lý ảnh offline, không cần model hoặc weights. Mỗi tín hiệu được so với median cùng cảnh rồi chia cho đơn vị lỗi tương ứng. Điểm ảnh là giá trị lớn nhất trong bốn tín hiệu.
- So với ngưỡng toàn cục, median cùng cảnh giảm ảnh hưởng của ánh sáng nền và độ nét đặc trưng của camera. Nếu phần lớn ảnh cùng cảnh bị lỗi giống nhau, mốc median có thể làm tín hiệu lỗi yếu đi.

## 04 · Baseline vs PoC

- Baseline dùng `-log(lap_var + 1e-6)` và ngưỡng phân vị 0,75 trên dev. Mã nguồn ở `src/baselines/laplacian_baseline.py`, commit `24afc16`.
- PoC dùng độ giảm log Laplacian và log độ sáng, độ tăng tỷ lệ pixel bão hòa trong ô lưới 4×8, và độ tăng blockiness tại biên 8 pixel. Mã nguồn ở `src/pipeline.py`, commit `24afc16`.
- Hai lượt chạy dùng cùng tập ảnh, seed 42 và ngân sách B=20. Cả hai xuất dự đoán cho toàn bộ fixture. Evaluator chỉ lấy ảnh thuộc split eval.

**Bảng 1 · Baseline vs PoC & hard slices**

| Metric (unit) | Baseline | PoC | Δ & chú thích |
|---|---:|---:|---|
| Defect Recall@B và Precision@B | Chờ GT | Chờ GT | Chưa có kết quả evaluator |
| False alarm theo điều kiện sáng | Chờ GT | Chờ GT | Cần nhãn ảnh hợp lệ trong từng slice |
| Review@B / miss / FP | B=20 | B=20 | Miss và FP cần GT |
| Runtime (giây/100 ảnh) | 0.637708 | 0.945208 | +0.307500 giây/100 ảnh |

Runtime chuẩn hóa từ hai tệp `out/baseline/run_info.json` và `out/poc/run_info.json`. Tổng thời gian mỗi lượt là 0.6122 giây và 0.9074 giây, gồm đọc dữ liệu và ghi đầu ra. Bảng so sánh chính thức do `src.compare` sinh sau khi có hai tệp metrics của evaluator.

## 05 · Evaluation protocol

- Đơn vị đánh giá là ảnh. Ghép dự đoán với GT theo `image_id`, lọc split eval rồi xếp theo `score` giảm dần. TP là ảnh lỗi thật trong top-B. FP là ảnh hợp lệ trong top-B. FN là ảnh lỗi thật ngoài top-B.
- Defect Recall@B = TP / (TP + FN). Precision@B = TP / B. Tập eval có 40 ảnh theo metadata. Số ảnh lỗi thực tế cần GT.
- Theo [metric contract](metric_contract.md), B=20. Đơn vị chuẩn hóa cho blur, dark, glare và compression lần lượt là 2,5, 1,0, 0,10 và 1,0. Ngưỡng RECAPTURE của PoC là 1,0.
- Metric contract dự kiến khoảng tin cậy Wilson 95%. Chưa tính vì thiếu nhãn của giảng viên. Lệnh evaluate ở mục 08.

## 06 · Result slices

Bảng dưới đếm số ảnh theo điều kiện sáng trong top-20 của từng phương pháp sau khi lọc split eval. N lấy từ `images.csv`. Hai cột kết quả lấy từ `ranked_review.csv` của từng lượt.

| Slice | N eval | Baseline | PoC | Ghi chú |
|---|---:|---:|---:|---|
| `light=day` | 20 | 9 | 10 | Số ảnh vào top-20 |
| `light=dusk` | 4 | 3 | 2 | Số ảnh vào top-20 |
| `light=night` | 16 | 8 | 8 | Số ảnh vào top-20 |

- `img_01_2` là ca khó trong hàng đợi vận hành. Một số xe vẫn thấy được, nhưng phần lớn chi tiết mặt đường bị chìm trong vùng tối. Quyết định duyệt là chụp lại. Chưa có GT để xác định đây có phải báo nhầm hay không.
- `img_05_3` và `img_20_3` có vùng lóa cục bộ gần xe. PoC đưa cả hai vào hàng đợi vận hành nhờ tín hiệu bão hòa theo ô. Quan sát này chỉ áp dụng cho hai ảnh đã xem.
- Với cam_B, ngưỡng Laplacian toàn cục có thể nhầm độ mềm quang học với mờ do lỗi. Độ chênh lỗi theo camera cần GT để xác minh.
- Trên máy ghi trong `run_info.json`, PoC mất 9.452083 ms/ảnh tính trên toàn lượt chạy.

**Kiểm ngoài tập T02.** Lệnh `python -m tools.check_t02_ood` lấy năm biến thể đầu theo ID từ mỗi cảnh gốc trong hai cảnh đủ mẫu đầu tiên của fixture T03 (`orig_02` và `orig_09`). Script xác minh manifest nguồn, giữ nhóm theo cảnh và chạy lại PoC với config T02. Kết quả gồm 6 đề xuất RECAPTURE và 4 đề xuất KEEP. [Kết quả từng ảnh](t02_ood_results.csv).

Đây là phép kiểm trên ảnh synthetic từ một fixture khác. Kết quả chỉ mô tả hành vi pipeline trên mẫu đã chọn. Phép kiểm giữ ngưỡng đã chốt và dùng tín hiệu lóa theo vùng lưới 4×8 hiện có. Việc hiệu chỉnh ngưỡng riêng theo camera chưa được kiểm chứng.

## 07 · Human & decision

- Hàng đợi vận hành `out/poc/review_queue.csv` gồm 20 ảnh có điểm nghi lỗi cao nhất trong lô 96 ảnh. Do Phuong Manh đã xem và xác nhận `approve` cho cả 20 ảnh. [Bản lưu hàng đợi đã duyệt](review_queue.csv).
- `approve` giữ đề xuất của pipeline. `reject` chuyển ảnh sang KEEP. `edit` cho phép đổi loại lỗi hoặc hành động. `out/poc/audit_log.jsonl` ghi người duyệt và trạng thái trước, sau khi áp dụng quyết định.
- Lô có 41/96 ảnh RECAPTURE (tỷ lệ ghi trong JSON là 0.4271). Do vượt ngưỡng 30%, kết quả là **REWORK**, mã `RECAPTURE_RATE_HIGH`. Owner là `Do Phuong Manh - reviewer`. [Kết quả cuối](decision.json) · [Nhật ký quyết định](decision_log.csv).
- HOLD áp dụng khi chưa duyệt đủ hàng đợi. REWORK áp dụng khi đã duyệt đủ và tỷ lệ chụp lại vượt ngưỡng. Các ảnh ngoài hàng đợi giữ dự đoán của pipeline.

## 08 · Reproduce & tests

```powershell
New-Item -ItemType Directory -Force out/poc | Out-Null
Copy-Item reports/review_queue.csv out/poc/review_queue_reviewed.csv
.venv/Scripts/python.exe -m src.main --config configs/lab.yaml --output out/baseline --run-name baseline --pipeline src.baselines.laplacian_baseline --offline --seed 42
.venv/Scripts/python.exe -m src.main --config configs/lab.yaml --output out/poc --run-name poc --offline --seed 42
.venv/Scripts/python.exe -m pytest -o addopts='' -q
```

Hai lệnh evaluate sau yêu cầu overlay GT của giảng viên.

```powershell
.venv/Scripts/python.exe -m src.evaluate --track t02 --pred out/baseline/ranked_review.csv --run-name baseline --out reports/metrics_baseline.json
.venv/Scripts/python.exe -m src.evaluate --track t02 --pred out/poc/ranked_review.csv --run-name poc --out reports/metrics_poc.json
.venv/Scripts/python.exe -m src.compare reports/metrics_baseline.json reports/metrics_poc.json --out reports/table1_baseline_vs_poc.md
```

- Kiểm thử ghi nhận 55 passed, 39 skipped, 0 failed. Kết quả từ lượt chạy tại repo cục bộ.
- Máy theo `run_info.json` gồm Python 3.11.9, `Windows-10-10.0.22631-SP0`, CPU `AMD64 Family 23 Model 104 Stepping 1, AuthenticAMD`. Git SHA `24afc16`.

## 09 · Limitations & ethics

- Kết quả hiện dựa trên ảnh synthetic CC0-1.0. Với ảnh thật, cần xác nhận quyền sử dụng và quy trình xử lý người, biển số hoặc dữ liệu nhận dạng.
- Rà soát top-B chỉ cho biết kết quả ở các ảnh đã chọn. Thiếu GT nên chưa đo được lỗi bỏ sót và chưa kết luận PoC tốt hơn baseline. Chưa đo tải khi quy mô tăng 10 lần.
- Cần người phụ trách dữ liệu xác nhận tiêu chí chất lượng, nguồn và quyền sử dụng trước khi áp dụng ngoài bài lab.
- Khi có GT, chạy evaluator trên cả hai phương pháp và phân tích riêng ảnh đêm, glare và nhóm camera.

## 10 · Team contribution

**Bảng 2 · Individual contribution evidence**

| Học viên | Git author/email | Module / vai trò | Commit SHA / diff chứng minh | Trình bày? |
|---|---|---|---|---|
| Do Phuong Manh (2A202603148) | ManhDo · dophuongmanh04@gmail.com | Pipeline, baseline, kiểm thử và rà soát T02 | `24afc16` dùng email Git cũ `dophuongmanh.khlmr@gmail.com`. Xem [bảng đóng góp](team_contributions.csv). | Có theo bảng đóng góp |

## 11 · References

| URL | Ngày xem | Ý nghĩa |
|---|---|---|
| [Đề T02](../docs/tracks/T02.md) | 14/11/2026 | Quy định baseline Laplacian, ngân sách review và các trường hợp lỗi cần phân tích. |
| [Metric contract](metric_contract.md) | 14/11/2026 | Ghi cách tách tập eval, ngưỡng và kế hoạch tính metric. |
| [Evaluator T02](../src/metrics/tracks.py) | 14/11/2026 | Xác nhận cách lọc split, ghép ảnh, xếp hạng và tính metric. |

---

**Bảng 3 · Checklist chốt khi nộp**

- [ ] Link repo ở mục 00 và hướng dẫn chạy ở README. Fixture mẫu là synthetic. Đối chiếu tag cuối trên repo dùng để nộp.
- [ ] Lệnh run, evaluate và tests đã ghi ở mục 08. Evaluate chờ overlay GT.
- [ ] Có failure cases và bằng chứng review, quyết định trong `reports/`. Còn thiếu metrics chính thức.
- [x] Có nguồn, license, split, seed, môi trường offline và mô tả phạm vi GT/eval.
- [x] Bài cá nhân có commit thực chất, nêu tại mục 10.
