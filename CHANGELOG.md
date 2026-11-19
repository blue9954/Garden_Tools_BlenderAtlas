# Changelog

Garden Atlas Tool의 버전 기록. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를, 버전은 [SemVer](https://semver.org/lang/ko/)를 따른다.
버전의 기준은 `garden_atlas_tool/blender_manifest.toml`의 `version` 하나뿐이다. 단위 테스트가 이 파일과 매니페스트 버전이 같은지 검사한다.

## [0.2.0] - 2026-11-19
명세 M2 + M3 (P2) 범위.

### 추가
- FR-26 오브젝트 · 머티리얼별 아틀라스 가중치(0.25~4.0, 텍셀 밀도 배수).
- FR-47 Unreal용 FBX 내보내기 옵션 (`SM_<이름>.fbx`).
- FR-48 설정 프리셋 JSON 저장 · 불러오기 (아틀라스 이름 제외, 잘못된 항목은 건너뜀).
- 노멀 16비트 옵션 (float 베이크, 16비트 PNG).
- M3 검토: 아틀라스 용량 검사. 소스 해상도를 담지 못하면 권장 해상도와 분할 장수를 경고. 검토 문서 `docs/M3_multi_atlas_review.md`.
- 아틀라스 매니페스트에 `tool_version`, `fbx`, `weights`, 텍스처별 `bit_depth` 항목.
- 테스트: `GA_DEVICE`로 GPU에서 통합 테스트 실행, 렌더 비교 인수 테스트(`harness.py ACC`).

## [0.1.0] - 2026-11-19
명세 M0 + M1 + M2 (P0 · P1) 범위의 첫 구현.

### 추가
- Extension 스캐폴드(`blender_manifest.toml`, Blender 5.1+), N-패널 Garden › Atlas.
- 검사(FR-01~06): 메시 필터, 슬롯 · UV · 스케일 · 라이브러리 검사, 타일링 · 겹침 검사, 머티리얼 분류.
- 비파괴 처리(FR-10~15): 복제본, 임시 머티리얼, 참조로 추적하는 롤백.
- UV(FR-20~28): UVAtlas 생성, 다중 오브젝트 패킹, UNIFORM / SOURCE 크기 기준, SEPARATE / KEEP_STACKED / REUNWRAP 겹침 처리, 통계, UV 미리보기와 베이크만 다시 실행.
- 베이크(FR-30~39): 프록시 · 네이티브 패스, 복합 셰이더 근사, AO, Emissive, Opacity, 모달 진행률과 ESC 취소.
- 출력(FR-40~46): Unreal BC / N(DirectX) / ORM / E 패킹, 결과 머티리얼, UV 정리, Join, JSON 매니페스트.
- 테스트 하네스 L0~L3, 통합 테스트 IT-01~15.

### 확인한 사항 (명세 13장 미확인 항목)
- `pack_islands`: 간격을 정확히 맞추려고 `ADD` 방식 + 스케일 보정 재패킹을 쓴다(`FRACTION`은 최대 90초).
- `merge_overlap=True`는 겹침을 유지한다.
- 적용 안 된 오브젝트 스케일은 `average_islands_scale`에 반영되지 않는다.
