# CLAUDE.md — remocon-pi

라즈베리파이 + `pigpio` 기반 **에어컨 IR 리모컨 녹화/분석/재생** 프로젝트.

## 오리엔테이션

작업 전 먼저 읽을 것 (프로젝트 구성·스크립트·문서 목록의 단일 소스):

- [README.md](README.md) — 개요, 빠른 시작, 스크립트/문서 목록
- [docs/](docs/) — `overview` / `hardware` / `usage` / `data-format`

> 스크립트·문서 목록 표는 README와 docs에만 둔다. 이 파일에 중복 작성하지 말 것.

## 코딩 규칙

- 배포/하드웨어 의존 값(pigpiod 호스트·포트, GPIO 핀, 캐리어/타이밍, 데이터 경로)은
  스크립트에 하드코딩하지 말고 `config.<상수>`를 참조한다. 데몬 연결은 `config.connect()` 사용.
  설정 항목 전체는 [docs/hardware.md](docs/hardware.md#설정--환경변수-configpy) 참고.

## ⚠️ 문서 최신화 규칙 (필수)

**코드를 변경하면 반드시 관련 문서를 같은 작업 내에서 갱신할 것.** 코드/문서 불일치를 남기지 않는다.

| 변경 내용 | 갱신할 문서 |
|-----------|-------------|
| 새 스크립트/기능 추가 | `docs/usage.md` + `README.md` 스크립트 표 |
| 핀·상수·하드웨어 변경 | `docs/hardware.md` |
| 설정값 추가/변경(`config.py`, 환경변수) | `config.py` + `.env.example` + `docs/hardware.md` 환경변수 표 |
| 데이터 형식·프로토콜 해석 변경 | `docs/data-format.md` |
| 파이프라인/구조 변경 | `docs/overview.md` |
| 문서 추가/삭제 | `README.md` 문서 표 즉시 동기화 |

작업 마무리 시: "변경한 코드에 대응하는 문서를 모두 갱신했는가?"를 자가 점검할 것.
