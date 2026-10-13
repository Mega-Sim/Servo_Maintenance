# Servo Maintenance RSA pack profile / SDO safety patch (0006)

> 대상: `Mega-Sim/Servo_Maintenance` 브랜치 `claude/nice-dirac-q56uun`.
> **전제:** `0005`(FoE 등) → **`0005b`(AbsoluteEncoderReset/UpdateServoParameter 함수 누락 보완)** → **`0006`(제품 코드/4축 구분/SDO 보호)** 순으로 적용한다. 이미 현장에 `0005b`에 해당하는 두 함수가 반영되어 있으면 `0005b`를 다시 적용하지 않고 `0006`만 적용한다.

## 동작 범위

| 제조사 (Vendor ID) | Product Code | 현재 구현 |
| --- | --- | --- |
| RSA `0x0000033D` | `0x02020012`, `0x02020013` (2축), `0x02010013` (1축), `0x02020002` (3축) | 기존 RSA 2+1+1 주소 변환 및 기존 AMC 유지보수 경로 유지 |
| RSA `0x0000033D` | **`0x02020033`** (4축 통합) | 4개 논리축/동일 bus/module 0–3 검증 후 4축 통합 전용 Index 처리 |
| Yaskawa `0x00000539` | `0x02200901` (Sigma-7), `0x02200902` (2축) | 모델 분기만 구현. `ERROR NOT_IMPLEMENTED` 반환 |
| LS Mecapion `0x00007595` | **실제 iX7MA4444B Product Code 확인 필요** | Product Code를 실제 EEPROM 값으로 전달하고 비교하는 분기 준비. 미검증 시 `LS_UNKNOWN_PRODUCT`; 기능은 `ERROR NOT_IMPLEMENTED` |

LS Product Code는 첨부 Controller 소스에 식별 값이 없으므로 임의 숫자로 입력하지 않았다. `LS_IX7MA4444B_PRODUCT_CODE`는 검증 전 기본값 0이며, 실제 ESI/EtherCAT 스캔 결과가 확인된 뒤 일치하는 코드를 정의해야 한다. Vendor ID만으로 특정 모델을 판정하지 않는다.

### RSA 4축 통합형 파라미터 Index

- Axis0: `0x2xxx` → SDO에 그대로 전달
- Axis1: `0x3xxx` → SDO에 그대로 전달
- Axis2: `0x4xxx` → SDO에 그대로 전달
- Axis3: `0x5xxx` → SDO에 그대로 전달
- 패키지 공통 소프트웨어 버전 `0x100A:00` / Save `0x1010:04`만 예외적으로 허용

참고: 제출된 `parameter.txt`에는 `0x2xxx/0x3xxx/0x4xxx`만 있어 4축형에서 Axis3(Slide)의 `0x5xxx` 파라미터는 찾을 수 없다. Axis3 자료는 추가 확인이 필요하다.

### 보호·로그

- SDO 메시지 배열 크기 16→128바이트(기존 메시지 union의 최대 크기 260바이트보다 작아 IPC 큐 envelope 크기는 유지).
- Upload/Download의 요청·수신 크기 검증 및 오류 시 `-1` 응답 유지.
- EC-Master 실패 로그에 요청 Index / 실제 Index / SubIndex / 요청·수신 길이 / EC 오류 코드 추가.
- `PARAM_READ`의 총 시도·성공·실패·스킵 집계. 실패 항목이 있으면 성공 접두어 대신 `ERROR` 반환(Bridge의 오판 방지).
- 통합 4축 팩에 대한 FoE는 모델별 매핑 미검증으로 차단. **기존 RSA 2+1+1 FoE 경로 유지.**
- AMC 별도 유지보수 스레드/UNIX 소켓 및 OHT Main 1ms 통신 설계는 그대로 유지.

## 반드시 필요한 gRPC 코드 재생성

Product Code는 EC-Master가 실제 슬레이브에서 읽은 값을 기존 `requestSlave` gRPC 응답의 **새 필드 #9**로 전송한다.

- `src/ecmaster/rpc/ecmaster.proto`
- `src/ecmaster/ecmaster_admin.cpp` (EC-Master 서버)
- `src/ecmaster/ecmaster_rpc_client.h` (AMC 클라이언트)

**이 3곳과 `EcServoMotor`를 함께 업데이트해야 한다.** Controller 빌드 환경에 이전 생성 버전 `ecmaster.pb.h/.cc`가 남아 있으면 컴파일 오류가 발생한다. 원래 빌드 환경에서 사용하는 것과 동일한 `protoc`로 재생성하라.

```bash
cd ~/work/master_control_19201/src/ecmaster/rpc
protoc --cpp_out=. ecmaster.proto
```

Service RPC 정의 자체는 바뀌지 않았기 때문에 기존 `ecmaster.grpc.pb.h/.cc`는 대개 그대로 사용할 수 있다. 단 생성기 버전과 Protocol Buffers 런타임 버전 일치 필수. 필요하면 `protoc --grpc_out=. --plugin=protoc-gen-grpc="$(command -v grpc_cpp_plugin)" ecmaster.proto`도 재생성한다.

## 적용 / 재빌드

기존 빌드가 성공한 소스 트리(이전 10개 수정 파일 보유)를 백업한 뒤, `0005b` 함수가 이미 들어간 트리라면 아래대로 `0006`만 적용한다:

```bash
cd ~/work/master_control_19201
git apply --check /path/to/0006-rsa-pack-profile-sdo-safety.diff
git apply /path/to/0006-rsa-pack-profile-sdo-safety.diff
cd src/ecmaster/rpc && protoc --cpp_out=. ecmaster.proto
cd ~/work/master_control_19201/build
cmake -DCMAKE_BUILD_TYPE=Release ..
make -j2
```

현재 시스템이 Git working tree가 아닌 경우 `git apply`는 그래도 파일을 패치할 수 있다. 현장 `.pb` 생성 환경에 `protoc`가 없으면 생성 단계에서 중단하고 기존 설치 파일과 빌드 체인을 확인한다. `sudo make`는 사용하지 않는다.

**주의:** EC-Master와 AMC 모두 새로운 gRPC/SDO 메시지 형식을 사용하도록 **같은 소스 기준으로 전체 재빌드 및 함께 재시작**해야 한다. 실행 파일 하나만 교체하면 버전이 맞지 않을 수 있다.

## 시험 순서

1. 2+1+1 기존 장비에서 `Connection Status` → `PROFILE=RSA_DUAL/RSA_SINGLE`, Product Code 실제값 확인; 기존 버전 조회 등 회귀 시험.
2. 4축 통합형에서 모두 `PROFILE=RSA_4AXIS`와 Product Code `0x02020033` 확인.
3. Servo OFF 상태에서 각 축 버전 조회(4축 공통 버전으로 출력될 수 있음).
4. 파라미터 읽기는 3~5행 정도의 **조회 전용, 해당 축 `0x2/3/4/5xxx` 파일**로 시험 후 전체 파일 확대. `TOTAL/OK/FAIL/SKIP` 확인.
5. Yaskawa/LS는 STATUS에서 Product Code/PROFILE만 표시하고 실제 유지보수 기능은 `ERROR NOT_IMPLEMENTED`로 남겨 둠.
6. Firmware Update / Absolute Encoder Reset / Parameter Update는 읽기 전용 검증 및 안전성 승인 전 실행 금지.

### 현재 검증 수준

- 패치 기계적 적용(`git apply --check`) 및 패치 내용 일치 확인 완료.
- 제품 코드/4축 Index 규칙의 정적 테스트 수행.
- **실제 OHT 빌드 및 EtherCAT 서보팩 통신 시험 미실시** (현재 실행 환경에 Controller의 Acontis/gRPC SDK와 `protoc`가 없음).
## 브랜치에서 직접 수정 소스 가져오기

현재 브랜치의 `controller_patch/src/`에는 **0005+0005b+0006 반영 수정 11개 파일**이 개별 원본 형태로도 저장돼 있다. 이미 이 소스를 복사해서 적용할 경우 `0005b`, `0006`을 **다시 적용하지 말 것**(중복 변경). `controller_patch/src/`의 기존 EC-Master FoE 관련 파일도 유지돼 있다.

유지보수 명령의 신규 처리 함수는 AMC에 있고, EC-Master는 기존 CoE 전달을 이용한다. 신규 모델 분기와 Product Code 전달은 `ecmaster.proto` 응답 필드 추가가 필요하므로 빌드 전에 `protoc` 재생성이 필수다.
