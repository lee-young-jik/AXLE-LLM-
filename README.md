# AXLE + LLM Auto Prover Demo

Lean 정리를 입력하면 `LLM 생성 -> AXLE 검증 -> (옵션) AXLE repair -> 재시도` 루프로 자동 증명을 수행하는 데모 프로젝트입니다.

## 핵심 구조

- 프론트엔드: `index.html`, `app.js`, `styles.css`
- 서버(로컬): `web_prover.py`
- 서버(버셀): `api/index.py`
- 자동 증명 루프 엔진: `autoprove.py`

## 보안 원칙

- 저장소에 실제 API 키를 하드코딩하지 않습니다.
- 웹 앱은 **사용자가 직접 입력한 키**만 사용합니다.
  - OpenRouter: `sk-or-v1-...`
  - AXLE: `pk_...`
- 키 승인(검증) 성공 전에는 실행이 차단됩니다.
- 실행 결과 저장 시 키는 마스킹됩니다.

## 로컬 실행

### 1) 설치

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) 웹 앱 실행

```bash
source .venv/bin/activate
python web_prover.py --host 127.0.0.1 --port 8787
```

브라우저:

- `http://127.0.0.1:8787`

사용 순서:

1. OpenRouter 키 입력 -> 승인
2. AXLE 키 입력 -> 승인
3. 모델 선택
4. 정리 입력 후 실행

## CLI 자동 증명 실행

CLI는 환경변수 방식입니다 (`.env.example` 참고).

```bash
source .venv/bin/activate
export AXLE_API_KEY="YOUR_AXLE_KEY"
export OPENROUTER_API_KEY="YOUR_OPENROUTER_KEY"
export OPENROUTER_MODEL="openai/gpt-4.1-mini"

python autoprove.py \
  --problems problems_singleton.yaml \
  --llm-provider openrouter \
  --max-attempts 6 \
  --use-repair \
  --verbose-attempts \
  --output-dir outputs/singleton
```

## Vercel 배포

이 저장소는 Vercel 서버리스 라우팅을 포함합니다.

- `vercel.json`
- `api/index.py`

배포 후 주의:

- 프로젝트 `Deployment Protection`이 켜져 있으면 외부에서 인증 페이지가 뜹니다.
- 공개 데모가 목적이면 Production 보호 정책을 확인하세요.

## GitHub 업로드 전 점검

### 1) 민감정보 패턴 스캔

```bash
rg -n "sk-or-v1-|pk_[A-Za-z0-9_-]{10,}|vcp_[A-Za-z0-9_-]{10,}|OPENROUTER_API_KEY|AXLE_API_KEY|VERCEL_TOKEN" -S .
```

### 2) 불필요 산출물 제외

`.gitignore`, `.vercelignore`에 아래가 포함되어야 합니다.

- `.venv`, `.venv311`, `node_modules`, `.npm-cache`
- `outputs`, `__pycache__`, `.vercel`
- `.env`, `*.log`

### 3) 토큰 회수 권장

토큰/키를 채팅이나 로그에 노출했다면 즉시 폐기 후 재발급하세요.

## GitHub 올리기 (새 저장소)

```bash
cd /path/to/your/clean-repo
git init
git add .
git commit -m "Initial: AXLE + LLM auto prover demo (sanitized)"
git branch -M main
git remote add origin https://github.com/<YOUR_ID>/<REPO>.git
git push -u origin main
```

## 라이선스

필요한 라이선스를 추가해 사용하세요 (예: MIT).
