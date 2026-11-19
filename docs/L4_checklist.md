# L4 수동 검증 체크리스트

환경: macOS, Apple M5 Max, **Blender 5.3.0**, **Unreal Engine 5.8**. 2026-11-19 기준.
사람이 화면에서 하던 확인은 실제 창과 실제 이벤트로 돌리는 스크립트로 바꿨다. 다시 돌리는 명령은 각 항목 옆에 적었다.

## Blender
- [x] **GUI 패널 · 검사 · 모달 실행**: `blender --factory-startup --python tests/integration/gui_smoke.py`
- [x] **ESC 키로 취소**: `blender --factory-startup --enable-event-simulate --python tests/integration/gui_l4.py` (A)
  - 실제 창 이벤트 ESC를 "베이크: Roughness" 도중에 보냄 → 실행 취소. 복제본 · 임시 머티리얼 · 이미지 · 파일이 남지 않고, 선택과 활성 오브젝트가 복원되며, 리포트에 취소가 기록됨.
- [x] **UV 에디터에서 UV 수정 → 베이크만 다시 실행**: 같은 스크립트 (B)
  - UV 미리보기 → 3D 뷰를 UV 에디터로 바꾸고 아일랜드를 90° 회전(`transform.rotate`) → "베이크만" 실행. 구운 색이 바뀐 UV를 따라감(오차 0/255).
- [x] **GPU 베이크(Metal)**: `GA_DEVICE=METAL python3 tools/harness.py L2` → 22/22
- [x] **원본과 결과 나란히 비교**(오브젝트 5 · 머티리얼 11): `python3 tools/harness.py ACC` → 평균 1.1/255, p95 3/255
- [x] **FBX 재임포트**(Blender): 슬롯 1개, UV `UVAtlas` 1개 (`P2_normal16_fbx`)
- [~] **Blender 5.1.x 설치 · 활성화 · 비활성화 · 재활성화**: 사용자 결정으로 5.3.0으로 대신 확인함(zip 설치는 L3 validate/build, 등록 · 해제 · 재등록은 IT-01). 5.1에서는 실행하지 않음.
- [ ] **CUDA / OptiX 베이크**: BLOCKED — NVIDIA GPU가 없는 환경.

## Unreal
`python3 tools/harness.py UE` — Blender가 `SM_` FBX와 `T_` 텍스처를 만들고, **UE 5.8** `UnrealEditor-Cmd`(헤드리스 Python)가 임포트해서 검사한다. 14개 항목 모두 PASS.

- [x] **임포트 결과 머티리얼 슬롯 1개**: `M_UECheck` 1개.
- [x] **UV0 = UVAtlas**: UE 메시의 UV0 좌표 42개가 Blender `UVAtlas`(V 반전)와 모두 일치하고, 전부 0~1 안이다. 라이트맵 UV는 생성하지 않는 설정으로 임포트했다.
- [x] **스케일과 형태**: 800 × 200 × 200 cm (Blender 2 m 큐브 3개, 3 m 간격), 삼각형 36개.
- [x] **텍스처 설정**: `T_*_N`은 UE가 이름으로 TC_NORMALMAP을 자동 지정하고 녹색 반전이 꺼져 있다(DirectX 그대로). `T_*_ORM`은 TC_MASKS · sRGB 끔으로 바꾸고, BC는 sRGB.
- [x] **머티리얼 구성**: BC → Base Color, ORM R/G/B → AO/Roughness/Metallic, N → Normal(Normal 샘플러)로 머티리얼을 만들어 메시에 적용함.
- [~] **노멀 방향**: 데이터 단계로 확인했다. 베이크한 N이 DirectX(-Y) 규약이고(IT-06), UE가 녹색을 뒤집지 않으며, FBX의 노멀과 탄젠트를 그대로 쓰도록 임포트했다(`Import Normals and Tangents`). UE에서 화면으로 보는 확인은 하지 못했다. 헤드리스(-nullrhi)에서는 렌더링이 되지 않기 때문이다.

### 권장 Unreal 임포트 설정 (검증한 값)
| 대상 | 설정 |
|---|---|
| `SM_<이름>.fbx` | Static Mesh, Combine Meshes 켬, Generate Lightmap UVs 끔(UV1이 필요하면 켬), Normal Import Method = Import Normals and Tangents, Import Materials · Textures 끔 |
| `T_<이름>_BC` | 기본값(sRGB). 알파가 있으면 BC 알파를 Opacity / Opacity Mask에 연결 |
| `T_<이름>_N` | Normalmap 압축(이름으로 자동), Flip Green Channel 끔 |
| `T_<이름>_ORM` | Masks 압축, sRGB 끔. Masks 샘플러로 R=AO, G=Roughness, B=Metallic |

참고: `-nullrhi` 커맨드릿에서는 `get_num_uv_channels`가 0을 돌려준다(렌더 데이터를 만들지 않음). 그래서 UV 검사는 MeshDescription의 UV0 값을 직접 비교한다.
