# Servo Maintenance 로그 추적 가이드

## 적용 범위

- **Windows IE → Linux Web (18765)**: 요청 수신, 유효하지 않은 축/양식, IP/Origin 차단, Bridge 호출 성공/실패 및 소요시간.
- **Linux TCP Bridge (127.0.0.1:18766)**: 요청 검증, 동시 작업 거부, 업로드 파일 준비, UNIX 소켓 접속/응답, 작업 종료 및 오류 분류.
- **AMC 유지보수 전용 스레드**: `/tmp/amc_rsa_maintenance.sock` 생성·접속, 명령 시작/종료, Vendor/Product/Profile, Parameter Read 집계, FoE 거부·사전검사 실패.
- **EcServoMotor / EC-Master**: SDO 큐 전송/응답 실패, Index·SubIndex, SDO 원본/변환 Index·길이·에러코드; FoE 상태 전환과 OP 복구 결과.

**중요:** 기존 Main ↔ AMC 1ms 주기 처리나 EtherCAT PDO 처리 루프에는 새 반복 로그를 넣지 않았습니다. EC-Master는 기존 SDO 전송 서비스로 쓰며 FoE만 전송 절차가 별도입니다.

## 로그 위치

`run_linux.sh` 실행 경로에 상관없이 **Servo Maintenance 저장소 디렉터리** 아래에 기록됩니다.

- `logs/servo_maintenance_web.log`
- `logs/servo_maintenance_bridge.log`

Python 로그는 각각 최대 **2 MiB × 현재 파일 및 백업 3개**로 순환 저장됩니다. 콘솔에도 출력됩니다.

AMC와 EC-Master의 `SEP_LOGI`/`SEP_LOGE`는 **해당 Controller 프로그램의 기존 로그 출력·저장 방식**을 그대로 이용합니다. 이 변경으로 AMC/EC-Master의 별도 저장 위치나 신규 로그 파일은 만들지 않았습니다.

## 핵심 로그 키워드

| 단계 | 정상 | 비정상 / 주의 |
| --- | --- | --- |
| Linux Web | `WEB_LISTEN`, `WEB_START`, `WEB_END` | `WEB_BAD_REQUEST`, `WEB_BAD_AXIS`, `WEB_IP_REJECT`, `WEB_ORIGIN_REJECT`, `WEB_BRIDGE_FAIL` |
| Bridge | `BRIDGE_LISTEN`, `BRIDGE_START`, `BRIDGE_AMC_CALL`, `BRIDGE_UNIX_REPLY`, `BRIDGE_END` | `BRIDGE_REJECT`, `BRIDGE_BUSY`, `BRIDGE_SOCKET_FAIL`, `BRIDGE_UNEXPECTED`, `BRIDGE_PARAM_PARTIAL_FAILURE` |
| AMC | `AMC_SOCKET_READY`, `AMC_START`, `AMC_END`, `AMC_PARAM_SUMMARY` | `AMC_SOCKET_*_FAIL`, `AMC_PARAM_OPEN_FAIL`, `AMC_PARAM_PARSE_FAIL`, `AMC_PARAM_TYPE_FAIL`, `AMC_PARAM_SDO_FAIL`, `AMC_FW_SDO_FAIL`, `AMC_FOE_BLOCKED` |
| EcServoMotor | Slave Info (Product 포함) | `AMC_SDO_QUEUE_FAIL`, `AMC_SDO_REPLY_TIMEOUT`, `AMC_SDO_REPLY_BAD_CMD`, `AMC_SDO_RESULT_FAIL` |
| EC-Master | `FOE_START`, `FOE_STAGE`, `FOE_RESTORE_STATUS` | `SDO upload failed`, `SDO download failed`, `FOE_RESTORE_STATUS`의 비정상 결과 |

AMC는 유지보수 명령마다 `id`를 부여해 **해당 AMC 로그 파일 내부**의 시작·종료·실패 위치를 연결합니다. Bridge에도 별도 `id`가 있지만, **두 ID는 같은 값이라는 보장이 없습니다.** 두 프로그램 사이 관계는 요청 `cmd/axis`와 각 로그의 **자체 기록 순서**로 확인해야 합니다.

## 현장 점검 순서

1. Linux 로그 `WEB_START`가 있는지 확인. 없으면 IE→Web 요청, IP/Origin, 파일 업로드 양식을 확인합니다.
2. `BRIDGE_START`가 있는지 확인. 없다면 Web→Bridge TCP 연결 또는 유효성 검사 실패를 확인합니다.
3. `BRIDGE_UNIX_CONNECT` / `BRIDGE_UNIX_REPLY`를 확인. 응답이 없다면 소켓 경로·권한·AMC 생존 여부를 확인합니다.
4. AMC `AMC_START` / `AMC_END`, `AMC_PARAM_SUMMARY`를 확인합니다.
5. 실제 SDO 실패는 `AMC_SDO_*`와 EC-Master `SDO upload failed`의 Index, Module, Product, 길이, CoE 오류코드로 분석합니다.
6. FoE 장애는 `FOE_STAGE`에서 마지막 성공 단계, `FOE_RESTORE_STATUS`에서 OP 복구 여부를 확인합니다.

**서로 다른 로그의 시계가 일치한다는 가정으로 시간순 인과관계를 판단하지 마십시오.** 각 로그 파일 내부 이벤트의 순서와 공통 명령·축·주소 정보를 종합해야 합니다.

## 로그 용량·안전

- Browser 토큰, 펌웨어 바이너리, 파라미터 값 전체는 로깅하지 않습니다.
- Web/Bridge는 시작/종료와 실패 정보를 기록하고, 파라미터 917개 각 행의 정상 성공은 일일이 출력하지 않습니다.
- AMC에서 파라미터 실패 상세 로그는 작업당 **최초 10건만** 출력하고, 나머지는 `AMC_PARAM_ERRORS_SUPPRESSED`로 수량을 남깁니다. SDO 실패 자체는 EC-Master에서도 확인 가능합니다.
- HTTP 요청 URL 전체, 사용자 제출 파일 내용 및 확인 문구는 로그에 출력하지 않습니다.
- 로그에는 장비 IP·제품 코드·SDO 주소 등이 나타나므로 외부 공유 전에 필요 시 가리십시오.
- `shared_token`이나 접속 허용 IP가 비어 있으면 보안 경고만 출력합니다. 테스트 설정을 배포 설정에 그대로 사용하지 마십시오.

## 테스트 및 재배포

```bash
cd ~/work/bin/servo_maintenance
python3 -m unittest tests.test_maintenance_observability -v
```

Web/Bridge 파일은 Python 프로세스 재시작 후 반영됩니다. **AMC, EcServoMotor, EC-Master C++ 로그 변경은 Linux 재빌드 후 반영**됩니다. 장비에서 Servo OFF·안전 정지 상태를 확인한 뒤 테스트하십시오.

실기 검증 미완료 항목: AMC/EC-Master 전체 빌드, RSA 2+1+1 회귀, RSA 4축 통합 SDO 응답, FoE 상태 복구. FoE 로그 추가는 기능 성공 검증과 다릅니다.
