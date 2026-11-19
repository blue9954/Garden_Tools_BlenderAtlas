# Garden Atlas Tool

Blender 5.1+ Extension. 선택한 여러 메시 · 머티리얼 슬롯을 Cycles 베이크로 텍스처 세트 1개 + 머티리얼 1개(Unreal 규격)로 합친다.
명세: `docs/Garden_Atlas_Tool_작업명세서.docx` (v0.1, 15장 미결 사항은 "가정" 값으로 구현).

## 설치
```bash
python3 tools/harness.py L3        # dist/garden_atlas_tool-0.1.0.zip 생성
```
Blender → Edit › Preferences › Get Extensions › ▾ › Install from Disk… 로 zip 설치.
3D 뷰포트 N-패널 **Garden** 탭 → **Atlas**.

## 사용
1. 합칠 오브젝트 선택 → **검사** (경고/오류 목록, 항목 클릭 시 해당 오브젝트 선택)
2. 이름 · 해상도 · 채널 · 출력 폴더 설정 (`//` 경로는 .blend 저장 필요)
3. (선택) **UV 미리보기** → UV를 손으로 고친 뒤 **베이크만 다시 실행**
4. **아틀라스 생성** — 진행률은 상태 표시줄, ESC로 취소(이번 실행 데이터 전부 정리)

결과: `ATLAS_<이름>` 컬렉션, `M_<이름>`, `T_<이름>_BC/N/ORM(/E).png`, `<이름>_atlas.json`, (옵션) `SM_<이름>.fbx`.

### P2 기능
- **가중치 (FR-26)**: UV 레이아웃 섹션에서 활성 오브젝트와 활성 머티리얼의 `아틀라스 가중치`(0.25~4.0)를 정한다. 텍셀 밀도 배수(선형)라서 2.0이면 두 배 선명하고 면적은 4배다. 오브젝트 값 × 머티리얼 값으로 적용된다.
- **FBX 내보내기 (FR-47)**: 결과 섹션의 `FBX 내보내기`. Unreal 기본값(-Z forward, Y up, Face 스무딩, 탄젠트 포함, 텍스처 경로 제외)으로 `SM_<이름>.fbx`를 출력 폴더에 저장한다.
- **설정 프리셋 (FR-48)**: 아틀라스 섹션의 `프리셋 불러오기 / 저장`. JSON이며 아틀라스 이름은 저장하지 않는다. 알 수 없는 항목이나 범위 밖 값은 건너뛰고 경고한다.
- **노멀 16비트**: 채널 섹션. 노멀을 float 이미지로 굽고 16비트 PNG로 저장한다(TGA는 8비트).
- **용량 검사 (M3 검토)**: 검사할 때 소스 텍스처 해상도를 담을 수 있는지 추정하고, 부족하면 권장 해상도와 분할 장수를 경고한다. → `docs/M3_multi_atlas_review.md`

## 구조
```
garden_atlas_tool/   core/ (bpy 없음, 단위 테스트)  bl/ (bpy 계층)  ops · ui · props · prefs
tests/unit/          L1 core 테스트 (pytest 또는 unittest)
tests/integration/   run_integration.py (L2, 헤드리스), gui_smoke.py (GUI 수동 스모크)
tests/spike/         사전 검증 스크립트
tools/harness.py     L0 정적 → L1 단위 → L2 통합 → L3 패키지
```

## 테스트
```bash
python3 tools/harness.py           # BLENDER=/path/to/blender 로 실행 파일 지정 가능
```
현재 결과 (Blender 5.3.0, macOS):
- L0 PASS · L1 33/33 · L2 22/22 (IT-01~15, 추가 3, M3 1, P2 3) · L3 PASS
- L2를 GPU로: `GA_DEVICE=METAL python3 tools/harness.py L2` → 22/22 PASS (Apple M5 Max)
- ACC(인수 비교, `python3 tools/harness.py ACC`): 오브젝트 5 · 머티리얼 11, 원본과 결과를 같은 카메라로 렌더해 비교 → 평균 차이 1.1/255, p95 3/255 PASS. 결과 이미지는 `dist/acceptance/`
- GUI 스모크(`blender --factory-startup --python tests/integration/gui_smoke.py`): 패널 그리기와 모달 실행 PASS
- L4: 일부 BLOCKED (Blender 5.1 실기, Unreal 임포트)

## 13장 미확인 항목 — 확정 결과
| 항목 | 결과 | 반영 |
|---|---|---|
| pack_islands margin과 픽셀 간격 | `ADD`: 간격 = 2 × margin × 패킹 스케일. `FRACTION`: 정확하지만 동일 아일랜드가 많으면 최대 90초 | `ADD`로 패킹 → UV 면적으로 스케일 측정 → margin 보정 재패킹 (IT-13: 설정 8px, 실측 8.97px) |
| merge_overlap=True 겹침 유지 | 유지됨 (미러 평면 겹침 > 50%) | KEEP_STACKED |
| 적용 안 된 스케일이 average_islands_scale에 반영되는지 | **반영 안 됨** (3배 스케일 → 밀도 비 0.333) | 스케일 적용 기본 켬, 끄면 검사에서 경고 |

## 구현 메모 / 명세와 다른 점
- 5.x에서 `mesh.materials.clear()`가 `material_index`를 지운다 → 복제본 슬롯은 제자리 교체.
- 복합 + 단순 머티리얼이 섞인 BaseColor: 각각 margin 0으로 따로 구운 뒤(베이크 알파 = 커버리지) 합성하고 numpy로 마진을 다시 확장.
- 해상도가 정사각형이 아니면 픽셀 비율을 유지하려고 회전을 끄고 u를 미리 압축해 패킹.
- 추가 프로퍼티: `ao_isolate`(AO 대상만, 기본 켬), `use_opacity`(자동 감지), `write_manifest`.
- 명세 P2(FR-26, 47, 48, 노멀 16비트)는 모두 구현했다. 다중 아틀라스 자동 분할은 검토 결과 v1에서는 하지 않는다(`docs/M3_multi_atlas_review.md`).
