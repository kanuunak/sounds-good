# S3: 내 화자 선택 CLI 구현 (v1.2)

첨부된 00_ARCHITECTURE.md의 계약을 따르는 `stages/speakers.py`를 작성하라.

## 요구사항

```python
def run(transcript_path: Path, audio_hash: str, cfg: dict) -> Path:
    """내 화자를 판정 → work/{hash}/03_speakers.json 생성. 존재하면 스킵."""
```

동작 순서:
1. 03_speakers.json 존재 → 스킵
2. CLI로 물어본다 (전역 기본 화자 옵션은 없다 — SPEAKER_00/01은 녹음마다 같은 사람에게
   고정되지 않으므로 매 수업 확인이 안전하다):

```
=== 화자를 선택하세요 ===

[SPEAKER_00] 발화 3개 (총 발화 187초):
  [00:05] Hello! How are you today?
  [00:31] That's great. So, did you finish the...
  [01:02] Let me give you some feedback on that.

[SPEAKER_01] 발화 3개 (총 발화 54초):
  [00:09] Um, I'm good. It was busy week.
  [00:38] Yes, I... I finished it yesterday.
  [01:15] Ah, okay, I see.

내 화자 번호는? [0/1]:
```

- 샘플은 각 화자의 **analyzable=true인 turn 중 길이가 긴 순 상위 3개**를 시간순으로 표시
  (짧은 맞장구·턴 경계 파편은 판별에 무용하고 오히려 헷갈리게 함)
- 총 발화 시간도 표시 (튜터가 보통 더 많이 말함 — 판단 보조)
- turn의 화자가 UNKNOWN뿐인 케이스(diarization_status="failed")는 main이 이 단계 전에 중단시키므로
  여기서는 발생하지 않는다. 방어적으로 만나면 예외를 발생시켜라.
- 입력 검증: 0/1 외 입력이면 재입력 요구

## 완료 기준

- [ ] 실제 02_transcript.json으로 실행 시 위 형식의 CLI 표시 후 03_speakers.json 생성
- [ ] 재실행 시 질문 없이 스킵
- [ ] 외부 의존 없음 (표준 라이브러리만)
