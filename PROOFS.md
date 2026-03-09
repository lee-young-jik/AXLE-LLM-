# Proof Summary (AXLE + LLM)

아래는 실제 데모에서 다룬 대표 명제 요약입니다.

## 1) Singleton Bound 핵심 산술형

명제:

```lean
theorem singleton_bound_from_sum_form (n k d : Nat)
    (h : k + d ≤ n + 1) : d ≤ n - k + 1 := by
  sorry
```

요약:

- LLM 1차 후보(`linarith`)는 실패
- AXLE 검증 피드백 반영
- fallback/산술 tactic(`omega`)로 최종 검증 통과

## 2) Hamming Bound 마지막 산술 단계

명제(핵심):

```lean
theorem hamming_bound_core (A q n d : Nat)
    (hpack : A * hammingBallVolume q n ((d - 1) / 2) ≤ q ^ n) :
    A ≤ q ^ n / hammingBallVolume q n ((d - 1) / 2) := by
  sorry
```

요약:

- 볼륨 양수 lemma 구성 후
- `Nat.le_div_iff_mul_le` 적용 형태를 맞추는 과정에서 반복 수정
- AXLE verify/repair 피드백을 반영해 최종 통과

## 3) 세제곱 합 항등식 데모

명제:

```lean
theorem sum_cubes_formula_demo (n : Nat) :
    4 * (Finset.range (n + 1)).sum (fun i => i ^ 3) = (n * (n + 1)) ^ 2 := by
  sorry
```

요약:

- 귀납법 + `Finset.sum_range_succ` 기반 접근
- 초기 시도에서 `rw` 대상 불일치 등 오류 발생
- Lean 에러를 다음 프롬프트에 반영하며 단계별 수정
- 최종적으로 Lean verified

## 루프 구조

1. LLM이 후보 증명 생성
2. AXLE `verify_proof` 호출
3. 실패 시 AXLE `repair_proofs` 호출(옵션)
4. 실패 메시지를 다음 시도 프롬프트에 삽입
5. 성공 시 최종 증명 채택
