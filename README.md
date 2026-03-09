# AXLE + LLM Demo (Lean Auto-Prover)

AXLE 검증 엔진과 LLM을 결합해 Lean 증명을 자동으로 시도하는 데모입니다.

- 루프: `LLM 후보 생성 -> AXLE verify -> (옵션) AXLE repair -> 재시도`
- 키 정책: OpenRouter/AXLE 키 모두 **사용자 직접 입력 + 승인** 후 실행

## 배포 링크

- Production: https://lean-beryl.vercel.app

## 배포 화면 예시

> 동적 썸네일 링크를 사용합니다.

![Demo Home](https://image.thum.io/get/width/1400/https://lean-beryl.vercel.app)

## 증명 결과 요약

자세한 증명 요약은 [PROOFS.md](./PROOFS.md) 참고.

## 핵심 파일만 보기

- `index.html` : 데모 UI
- `app.js` : 프론트 동작(키 승인, 실행 루프 표시)
- `styles.css` : UI 스타일
- `web_prover.py` : 로컬 서버 + AXLE/LLM 루프
- `api/index.py` : Vercel 서버리스 엔드포인트
- `autoprove.py` : 공통 루프/프롬프트 로직
- `vercel.json` : 배포 라우팅 설정
- `requirements.txt` : Python 의존성

## 로컬 실행

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python web_prover.py --host 127.0.0.1 --port 8787
```

브라우저: `http://127.0.0.1:8787`

## 보안 주의

- 실제 키를 코드/README에 넣지 마세요.
- `.env`, `.vercel`, `outputs`, `.venv` 등은 Git 추적 제외 설정되어 있습니다.
- 키가 외부에 노출되면 즉시 rotate(폐기/재발급) 하세요.
