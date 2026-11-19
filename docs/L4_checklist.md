# L4 수동 검증 체크리스트

상태: **일부 BLOCKED.** 이 환경(macOS, Blender 5.3.0, Apple M5 Max)에서 자동으로 확인한 항목은 [x]로 표시했다.

- [x] GUI에서 패널 그리기 · 검사 · 모달 실행 (`tests/integration/gui_smoke.py`)
- [x] GPU 베이크(Metal)가 CPU와 같은 결과 — `GA_DEVICE=METAL`로 L2 22/22 통과
- [x] 오브젝트 5개 · 머티리얼 11개 씬에서 원본과 결과를 렌더해 비교 — 평균 1.1/255, p95 3/255 (`harness.py ACC`)
- [x] 베이크 중 취소 시 정리 — 제너레이터 close 경로(IT-10)로 확인. GUI에서 실제 ESC 키 입력은 확인하지 못함
- [x] 결과 FBX를 다시 Blender로 임포트 → 슬롯 1개, UV `UVAtlas` 1개 (`P2_normal16_fbx`)
- [ ] **Blender 5.1.x**에 zip 설치, 활성화 · 비활성화 · 재활성화 (이 환경에는 5.3만 있음)
- [ ] GUI에서 ESC 키로 취소
- [ ] UV 에디터에서 손으로 UV 수정 → 베이크만 다시 실행 (API 경로는 IT-06에서 확인)
- [ ] CUDA / OptiX (NVIDIA) 베이크
- [ ] Unreal 임포트: 머티리얼 슬롯 1개, UV0 = UVAtlas
- [ ] Unreal: `T_*_N`을 Normal map(DirectX), `T_*_ORM`을 Masks(sRGB 끔)로 → 노멀 방향 올바름
