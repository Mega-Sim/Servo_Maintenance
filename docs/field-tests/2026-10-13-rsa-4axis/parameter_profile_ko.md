# parameter.txt 입력 구조 분석

파일명: `parameter.txt` (사용자가 업로드한 실제 파일의 구조/통계만 수록)
- **917행**, 각 행 **6열**, 헤더 없음.
- 열: `Name, ParameterNo, Index, SubIndex, DataType, Value`.
- Type 개수: `USINT=517`, `UINT=304`, `UDINT=54`, `DINT=30`, `INT=9`, `SINT=3`.
- Index 첫 16진 자리: `0x2xxx=330`, `0x3xxx=294`, `0x4xxx=293`.
- 파일의 307~612행 사이에도 `0x2206`, `0x2305`, `0x2315`, `0x250A` 같은 0x2xxx 값이 존재하며, 613행 이후에도 같은 사례가 있음. 해당 파일을 기계적으로 특정 축 전용으로 해석하면 안 됨.

## 데이터 값 비공개 대표 형식
```csv
Fault and Disable Braking,0.02 D0,0x2002,1,USINT,<redacted>
Inertia Ratio,0.04,0x2004,-,UINT,<redacted>
Acceleration,2.06,0x2206,-,DINT,<redacted>
Fault and Disable Braking,0.02 D0,0x3002,1,USINT,<redacted>
Fault and Disable Braking,0.02 D0,0x4002,1,USINT,<redacted>
```

### 검토 핵심
- 파일 자체는 6열로 파싱 가능해 보이지만, `PARAM_READ` 소스는 선택 Axis 하나에 대해 **모든 917행**을 그대로 읽음.
- SDO Index와 `module_num` 오프셋이 중복 적용될 수 있으며, 일부 Index는 실제 반환 데이터 크기와 CSV 타입 표기가 다를 수 있음.
- **0x2/3/4 범위만으로 이 CSV의 축 배정을 단정하지 말고** 실제 장비 ESI/OD 및 기존 제조사 Tool 내보내기 규칙과 비교할 것.
- 민감한 장비 튜닝 값이 포함돼 원본 파일 전체는 공개 리포지토리에 등록하지 않음.
