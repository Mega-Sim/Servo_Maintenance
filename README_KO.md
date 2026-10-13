# Linux Web 기반 Servo Maintenance — EC-Master FoE 방식

## 구현 결과

Windows IPC는 브라우저 표시, 버튼 입력, 파일 전송, 결과 표시만 담당합니다. 실제 상태 조회, 절대치 엔코더 리셋, 파라미터 처리, 펌웨어 업데이트는 Linux에서 수행합니다.

펌웨어 업데이트는 별도 EtherCAT 프로그램을 실행하거나 `ecmaster`를 종료하지 않습니다. 실행 중인 EC-Master 프로세스 내부에서 대상 Slave를 `INIT → BOOTSTRAP`으로 전환하고 `emFoeFileDownload()`를 호출한 뒤 `INIT → PRE-OP → OP`로 복귀합니다.

| 구간 | 주소/인터페이스 | 역할 |
| --- | --- | --- |
| Windows → Linux Web | `http://192.168.0.2:18765/` | UI 표시와 사용자 요청 |
| Linux Web → Bridge | `127.0.0.1:18766` | 인증된 내부 요청 전달 |
| Bridge → AMC | `/tmp/amc_rsa_maintenance.sock` | 명령 및 결과 전달 |
| AMC → EC-Master | SysV message queue | SDO/FoE 실행 요청 |
| 업로드 저장 위치 | `/home/semes/work/bin/<원본파일명>` | 파라미터·펌웨어 파일 보존 |

## 지원 기능

| 기능 | Linux 실제 처리 경로 |
| --- | --- |
| 통신 상태 | AMC `STATUS` |
| 펌웨어 버전 | SDO `0x100A:0` |
| 절대치 엔코더 리셋 | 기존 `AbsoluteEncoderReset()` |
| 파라미터 읽기/변경 | 기존 `PARAM_READ`, `UpdateServoParameter()` |
| 펌웨어 업데이트 | EC-Master FoE, password `0x01020304` |

펌웨어 대상은 Driving=axis 0, Hoist=axis 2, Slide=axis 3입니다. Front/Rear는 하나의 Driving Dual Servo Pack을 공유합니다.

## 파일 구성

| 경로 | 내용 |
| --- | --- |
| `linux/servo_maintenance_web.py` | Linux에서 최신 HTML UI를 제공하는 웹 서버 |
| `linux/servo_maintenance_tcp_bridge.py` | 웹 요청 검증, 파일 저장, AMC 전달 |
| `linux/servo_maintenance.ini` | Web/Bridge/AMC 연결 설정 |
| `linux/*.service.template` | 부팅 시 Web과 Bridge 자동 시작용 systemd 템플릿 |
| `controller_patch/src/` | 변경된 최신 controller 전체 소스 파일 |
| `changes/0005-ecmaster-foe-firmware-update.diff` | 최신 `master-controller-260901` 기준 diff |
| `windows/Servo_Maintenance.url` | Windows IPC 브라우저 바로가기 |
| `tests/` | 프로토콜·웹·브리지·소스 계약 테스트 |

## 1. Controller 소스 적용 및 빌드

현재 운용 중인 controller 소스를 별도 백업한 뒤 진행하십시오.

```bash
cd /home/semes/work/master_control
patch --dry-run -p1 < /경로/Servo_Maintenance_Linux_Web_20261013/changes/0005-ecmaster-foe-firmware-update.diff
patch -p1 < /경로/Servo_Maintenance_Linux_Web_20261013/changes/0005-ecmaster-foe-firmware-update.diff
```

diff 기준은 업로드된 최신 `master-controller-260901.zip`입니다. 현장 소스가 다르면 dry-run 실패 내용을 확인하고 수동 병합해야 합니다.

그다음 현장에서 사용하던 기존 빌드 절차로 `ecmaster`와 `amc_oht`를 다시 빌드·배포합니다. 두 프로세스를 정상 순서로 기동하고 AMC 소켓을 확인합니다.

```bash
test -S /tmp/amc_rsa_maintenance.sock && echo "AMC socket ready"
```

## 2. Linux Web/Bridge 설치

```bash
cd /home/semes/work/bin
cp -a /경로/Servo_Maintenance_Linux_Web_20261013 ./servo-maintenance
```

`linux/servo_maintenance.ini`에서 다음 값을 현장에 맞게 수정합니다.

```ini
[server]
shared_token = 16자_이상_임의_문자열

[connection]
shared_token = 위와_동일한_문자열

[web_ui]
allowed_browser_ip = 192.168.0.3
```

Linux 보드 IP가 `192.168.0.2`, Windows IPC IP가 `192.168.0.3`이라는 전제입니다.

### 수동 실행

한 번에 Web과 Bridge를 실행하려면 다음 명령 하나를 사용합니다.

```bash
cd /home/semes/work/bin/servo-maintenance
chmod +x run_linux.sh
./run_linux.sh
```

개별 진단이 필요할 때만 두 터미널에서 각각 실행합니다.

```bash
python3 linux/servo_maintenance_tcp_bridge.py --config linux/servo_maintenance.ini
python3 linux/servo_maintenance_web.py --config linux/servo_maintenance.ini
```

### 부팅 시 자동 실행

아래 서비스는 Web/Bridge만 자동 시작합니다. 현장의 기존 `ecmaster`와 `amc_oht` 시작 순서는 그대로 유지해야 합니다.

```bash
sudo install -m 0644 linux/servo-maintenance-bridge.service.template /etc/systemd/system/servo-maintenance-bridge.service
sudo install -m 0644 linux/servo-maintenance-web.service.template /etc/systemd/system/servo-maintenance-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now servo-maintenance-bridge.service servo-maintenance-web.service
sudo systemctl status servo-maintenance-bridge.service servo-maintenance-web.service
```

로그와 포트 확인:

```bash
sudo journalctl -u servo-maintenance-bridge.service -u servo-maintenance-web.service -n 100 --no-pager
ss -ltn | grep -E '18765|18766'
```

정상 상태는 `18765`가 `0.0.0.0`에, `18766`이 `127.0.0.1`에만 열립니다.

## 3. Windows IPC 사용

Windows에는 Python을 설치하거나 별도 프로그램을 실행할 필요가 없습니다.

1. `windows/Servo_Maintenance.url`을 Windows 바탕화면에 복사합니다.
2. 바로가기를 더블 클릭합니다.
3. Internet Explorer에서 `http://192.168.0.2:18765/`가 열리는지 확인합니다.

## 4. 펌웨어 업데이트 절차

1. OHT를 완전히 정지시키고 작업 가능 상태인지 확인합니다.
2. UI에서 통신 상태와 현재 펌웨어 버전을 확인합니다.
3. Driving, Hoist, Slide 중 물리 Servo Pack을 선택합니다.
4. 검증된 `.bin` 파일을 선택하고 확인 문구 `FIRMWARE_UPDATE`를 입력합니다.
5. 실행하면 파일이 `/home/semes/work/bin/<원본파일명>`에 저장됩니다.
6. AMC가 모든 축의 모션을 차단하고 Servo OFF를 요청합니다.
7. EC-Master 내부에서 대상 Slave에 FoE를 실행하고 네트워크를 OP로 복구합니다.
8. 완료 후 `0x100A:0` 버전 값을 UI에 표시합니다.

동일한 파일명이 이미 있고 내용도 같으면 기존 파일을 재사용합니다. 같은 이름에 다른 내용이면 덮어쓰지 않고 요청을 거부합니다.

## 안전 및 현재 검증 범위

- 펌웨어 업데이트 중에는 절대로 운전 명령을 보내면 안 됩니다.
- EC-Master 프로세스는 종료하지 않지만 대상 Slave의 상태 전환으로 해당 Servo Pack은 제어 불가 상태가 됩니다.
- FoE 성공 후 Servo reset object는 Driving Dual Pack에 `0x2C10:0`, Hoist/Slide에 `0x3010:0`을 사용합니다.
- 소스 자동 테스트와 Acontis 라이브러리의 FoE 심볼 존재 여부는 확인했습니다.
- 현재 작업 환경에는 실제 OHT/Servo Pack과 controller 빌드 의존 패키지가 없어 실장비 FoE 전송 및 전체 바이너리 빌드는 수행하지 못했습니다. 최초 현장 적용은 검증용 장비에서 진행해야 합니다.

## Git 저장소 시작

개발 작업 폴더는 `main` 브랜치의 Git 저장소로 초기화되어 있습니다. 배포용 ZIP은 `.git`을 제외하므로, ZIP을 푼 위치에서 새 저장소를 시작하려면 `GIT_INIT_KO.md`를 따르십시오. 함께 제공되는 Git bundle을 clone하면 이번 개발 이력까지 그대로 복원할 수 있습니다.
