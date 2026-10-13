# 2026-10-13 RSA 4축 통합 OHT Servo Maintenance 현장 1차 분석

> 기준 브랜치: `claude/nice-dirac-q56uun` / 사용자 실제 테스트는 이 브랜치의 Controller diff 및 이전에 보완한 `SingleAxis::AbsoluteEncoderReset`, `UpdateServoParameter` 반영 빌드.
> **본 문서는 분석/재현 기록이며 코드 수정 또는 실기 안전 검증 완료를 의미하지 않음.**

## 대상 구성

- **서보 하드웨어: RSA 4축 통합 서보팩 1개**, 별도의 2축+1축+1축 팩 3개 구성이 아님.
- `250106_RSA_4Axis.xml`, `250106_RSA_4Axis_model.yml`.
- EC-Master 로그: `RSA Quadruple-Servo Detected`; 서보 논리 slave_id 0,1,2,3은 **모두 Station Address 1001**. IO는 Station 1005,1006.
- AMC `ec_servo_motor.cpp:99`: axis_0→module_num 0, axis_1→1, axis_2→2, axis_3→3; 전부 bus_index 0.
- OHT Axis: 0 Driving Front, 1 Driving Rear, 2 Hoist, 3 Slide.

## 결과

| 구분 | 결과/증거 | 판정 |
| --- | --- | --- |
| IE에서 Linux Web UI 표시 | `http://192.168.0.2:18765/` | 성공 |
| Windows→Web→Bridge→AMC | `OK / AMC CONNECTED` 및 모든 Axis `ECAT=CONNECTED SERVO_ON=0` | 연결 성공 |
| 실제 4축 SDO 읽기 전반 | 축별 Firmware Version 결과/SDO 로그 | 일부 실패 |
| Slide (Axis 3) Firmware Version | EC-Master `Fail to upload sdo to slave 3` 반복 | 실패 확인 |
| Parameter Read | 사용자 재현 실패 및 EC-Master에서 다량의 slave 0 SDO 실패·CoE `Size mismatch` | 실패 확인, 복수 원인 가능 |
| Parameter Update / ABS Reset / FoE | 현장에서 실행 성공 확인 없음 | **미검증** |

**중요:** STATUS의 `ECAT=CONNECTED`는 `motors[i]->initialized_` 결과로, 개별 SDO Index 읽기 성공을 증명하지 않음. 서로 다른 로그 파일의 타임스탬프는 상호 비교하여 인과 순서를 추정하지 않음.

## 원인 후보 A: 버전 SDO의 모듈별 오프셋 적용

`src/app/amc/amc_oht.cpp`의 `read_version(axis)`는 `coeSdoUpload(0x100A, 0x00, ...)` 호출.

`src/ecmaster/ecmaster_message_handler.cpp`의 `EcMasterSvc::handleSdoMessage`:
```cpp
int sdo_offset = 0x800;
if (slave.vendor_id == ecvendor_rsa) {
    if (msg.sdo.index >= 0x2000 && msg.sdo.index < 0x3000)
        sdo_offset = 0x1000;
}
index = msg.sdo.index + slave.module_num * sdo_offset;
```

`0x100A`는 조건 범위 밖이어서 Slide(module_num=3) 실전송 Index는 **0x280A**가 됨. 해당 객체의 유효성/지원범위를 확인하지 못함. 이는 'Axis 3 전달 오류'가 아니라 **버전 객체 Index 변환 정책**을 우선 조사해야 한다는 근거. 무조건 0x100A로 예외처리하면 모든 축이 동일한 팩 공용 버전만 읽을 수 있으므로, OD/ESI 근거 없이 일괄 수정하지 말 것.

## 원인 후보 B: Parameter Read 파일과 축별 SDO 주소 해석 충돌

사용자 제공 `parameter.txt`는 헤더 없이 **917행×6열**(`Name,Parameter,Index,SubIndex,Type,Value`)이며, 0x2xxx/3xxx/4xxx Index들이 혼합. 특수하게 다른 블록에 `0x2206`, `0x2305`, `0x2315`, `0x250A` 등도 존재.

`amc_oht.cpp`의 PARAM_READ는 **UI에서 선택한 단일 axis를 유지한 채 파일 모든 줄**에서 `motors[axis]->coeSdoUpload(index,subindex,sizeof(type))` 실행. 파일을 통합 팩 전체 값으로 사용해도 축별 필터/주소 정규화 없음. EC-Master는 Index에 추가 모듈 오프셋을 적용하므로, 이미 절대 주소로 기록된 항목이면 중복 이동 위험이 있음.

같은 파일 예시(분석용 산술; 실제 서보 OD의 유효성 미확정):
- Slide axis3 + `0x2002` 요청 → `0x5002` (`0x1000 × 3`)
- Slide axis3 + `0x3002` 요청 → `0x4802` (`0x800 × 3`)
- Slide axis3 + `0x4002` 요청 → `0x5802` (`0x800 × 3`)

추가로 실제 EC-Master 로그에 `max. 1 expected and 2 read`, `max. 2 expected and 4 read` 크기 불일치가 확인됨. 파일의 `USINT/UINT` 가정 또는 조회되는 실제 OD 객체의 데이터 타입이 맞지 않을 가능성이 있음.

## 원인 후보 C: 긴 읽기 작업/출력과 파일 재업로드 규칙

- 전체 917개 SDO 조회를 하나의 동기화된 AMC 유지보수 명령으로 수행함.
- Bridge `_save_uploaded_file()`은 같은 파일명이 이미 존재하고 **내용이 다르면 업로드 거부**. 로그/원인 분리용 명확한 UI 에러 표시 필요.
- 다만 이번 자료에서는 **반복 SDO 실패/CoE Size mismatch가 확인되므로** 업로드 거부만으로 설명할 수 없음.
- 정확한 `PARAM_READ` 응답 전문과 어떤 axis/파일로 제출했는지는 추가 수집 필요.

## 재현/검증 계획 (순서)

1. **읽기 전용**으로 4개 축 STATUS, FW_VERSION 결과를 표로 확보하고 실패 시 `slave_id/module_num/bus_index/station_address/request_index/translated_index/subindex/request_len/EC_E_*` 코드 로깅.
2. 서보팩 ESI/XML/Object Dictionary에서 4축 통합형의 `0x100A` 및 0x2xxx/0x3xxx/0x4xxx/0x45xx 주소/타입 규칙 확인.
3. 파라미터 파일을 축별로 분리하거나 이미 절대 주소인 Index에 오프셋 중복 적용 방지; 시험은 몇 개의 **읽기 전용 항목**부터 시행.
4. 반환 타입/길이 자동 검증 및 행별 성공/실패/원인 집계; `READ_FAIL`만 찍지 말고 실제 CoE 에러코드와 Index 출력.
5. 현장 IE 사용자에게 917개 파일 전체 결과를 충분히 보여줄 수 있도록 응답 길이, 완료 건수, 실패 건수 확인.
6. **Firmware Update는 4축 전체가 Station 1001을 공유하므로 대상 팩 매핑/중복 FoE 수행 방식 검증 전 차단**. Parameter Update/ABS Reset도 현장 승인 및 안전 정지 조건 검증까지 수행하지 않음.

## 부수 관찰 (주원인과 별개)

- EC-Master 로그 `Error: Set thread affinity, invalid CPU index 3`; AMC에서 CPU index 2 오류. 실시간성 검토 대상으로 분리.
- 일부 모델 파일 `Fail to load config from file` 메시지 존재. 현 로그의 SDO 조회 실패 원인으로 단정하지 않음.
- `Slave_1007` 텍스트와 실 Station Address 1001의 불일치는 모델/로그 라벨에 대한 후속 확인 항목.

## 첨부 자료 처리

원본 사용자 제공 자료: `IPC_HTML_UI.png`, `amc_lob.txt`, `ecmaster_lob.txt`, `parameter.txt`.
이 공개 저장소에는 **로그 원문 전체 및 화면 캡처 전체**를 싣지 않음(라이선스 키·로컬 PC 바탕화면 등의 식별 가능한 정보 노출 방지). 재현 가능한 비민감 로그 인용과 파라미터 구조 통계를 옆 파일에 포함.

## 추가로 확인된 소스상 판정 버그: PARAM_READ의 실패 은폐

`linux/servo_maintenance_tcp_bridge.py`의 `AMC_COMMANDS`는 `parameter_read: ("PARAM_READ {axis} {path}", "PARAM_READ Axis=")`로 성공 접두어를 판정함. 하지만 AMC `src/app/amc/amc_oht.cpp`는 각 SDO가 실패해도 출력 행에 `READ_FAIL`을 붙이고 **항상** 맨 앞에 `PARAM_READ Axis={axis}`를 내보냄. 따라서 **일부/전체 파라미터가 읽기 실패해도 Bridge가 `ok=true`로 판정할 수 있음**. 행별 성공/실패 집계 및 전체 성공 기준을 재정의해야 함.
