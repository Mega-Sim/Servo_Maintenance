/*
 * SEMES CONFIDENTIAL
 *
 * Copyright (c) 2017 SEMES Co.,Ltd.
 * All Rights Reserved.
 *
 * NOTICE: All information contained herein is, and remains the property
 * of SEMES and its suppliers, if any. The intellectual and technical concepts
 * contained herein are proprietary to SEMES and its suppliers and may be
 * covered by S.Korea and Foreign Patents, patents in process, and are
 * protected by trade secret or copyright law.
 * Dissemination of this information or reproduction of this material is
 * strictly forbidden unless prior written permission is obtained from SEMES.
 */


//
// Created by root on 17. 12. 11.
//
#include <yaml-cpp/yaml.h>
#include "oht_controller.h"

using namespace sephi;
using namespace sephi::oht;

OHTController::OHTController()
    : initialized_(false), thread_exit_(false), thread_(nullptr)
{
    loadIOStatus = false;
}

OHTController::~OHTController()
{
    finalize();
}

void OHTController::setToInitialPosition()
{
    std::lock_guard<std::mutex> guard(lock_);
    localizer_->setLocation(config_.initial_location.from, config_.initial_location.to, config_.initial_location.offset);
}

double OHTController::getPosmm(int axis)
{
    if (axis == OHT_HOIST) {
        return config_.hoist_unit_conv.pulse2mm( hoist_->getPos());
    } else {
        return motors_[axis]->getActual()[0];
    }
}

double OHTController::getPos(int axis)
{
    return motors_[axis]->getActPos();
}

double OHTController::getRemainingDistance()
{
    //return drive_->getRemainingDistance();
}

/*
OHTLocation OHTController::getLocation()
{
    return localizer_->getLocation();
}
*/
int OHTController::getMarkState()
{
    return localizer_->getMarkingState();
}

int OHTController::getRunState()
{
    //return drive_->getRunState();
}

bool OHTController::localize()
{
    //std::lock_guard<std::mutex> guard(lock_);
    //return drive_->runForLocalization();
}

bool OHTController::runToNode(uint32_t nodeid)
{
    //std::lock_guard<std::mutex> guard(lock_);
    //SEP_REQUIRE_RET(map_.node_map.find(nodeid) != map_.node_map.end(), false);
    //return drive_->runToNode(nodeid);
}

bool OHTController::runToEqStation(uint32_t stid)
{
    //std::lock_guard<std::mutex> guard(lock_);
    //SEP_REQUIRE_RET(map_.station_map.find(stid) != map_.station_map.end(), false);
    //return drive_->runToStation(stid);
}

bool OHTController::runDisplacement(int32_t disp_mm)
{
    //std::lock_guard<std::mutex> guard(lock_);
    //return drive_->moveDistance(disp_mm);
}

bool OHTController::stopRun(bool quick)
{
    //return drive_->stop(quick);
}

bool OHTController::load(bool step)
{
	/*
    std::lock_guard<std::mutex> guard(lock_);
    auto loc = localizer_->getLocation();
    auto sta = map_.getLink(loc.from, loc.to)->getStation(loc.offset);
    SEP_REQUIRE_RET(sta != nullptr, false);
    SEP_REQUIRE_RET(sta->teach != nullptr, false);
    return trans_->load(sta, step);
	*/
}

bool OHTController::unload(bool step)
{
	/*
    std::lock_guard<std::mutex> guard(lock_);
    auto loc = localizer_->getLocation();
    auto sta = map_.getLink(loc.from, loc.to)->getStation(loc.offset);
    SEP_REQUIRE_RET(sta != nullptr, false);
    SEP_REQUIRE_RET(sta->teach != nullptr, false);
    return trans_->unload(sta, step);
	*/
}

bool OHTController::transStop()
{
    //std::lock_guard<std::mutex> guard(lock_);
    //return trans_->stop();
}

bool OHTController::transOrigin()
{
    //std::lock_guard<std::mutex> guard(lock_);
    //return trans_->origin();
}

bool OHTController::transPause()
{
    //std::lock_guard<std::mutex> guard(lock_);
    //return trans_->pause();

}

bool OHTController::transResume()
{
    //std::lock_guard<std::mutex> guard(lock_);
    //return trans_->resume();

}

bool OHTController::transStepRun()
{
    //std::lock_guard<std::mutex> guard(lock_);
    //return trans_->step();

}


bool OHTController::initialize()
{
    SEP_REQUIRE_RET(loadConfig(), false);
    SEP_REQUIRE_RET(initializeIO(), false);
    SEP_REQUIRE_RET(loadIOConfig(), false);
    SEP_REQUIRE_RET(initializeServo(), false);
    SEP_REQUIRE_RET(makeBehaviors(), false);
    SEP_REQUIRE_RET(startService(), false);
    return true;
}

void OHTController::finalize()
{
    rear_wheel_->disableServo();
    writeDo(ioConfig_.getportid("hoist_ext_brake_release"), false);  //writeDo(hoist_ext_brake_release, false);    // DO - 63
    writeDo(ioConfig_.getportid("driving_brake_release"), false);     //writeDo(driving_brake_release, false);
    stopService();
}

bool OHTController::initializeIO()
{
    front_io_ = make_unique<EcOHTFrontIo>();
    rear_io_ = make_unique<EcOHTRearIo>();

    SEP_REQUIRE_RET(front_io_->initialize(5), false);
    SEP_REQUIRE_RET(rear_io_->initialize(4), false);
    SEP_REQUIRE_RET(onboard_io_.initialize(), false);
    if (AMC_CLIENT_MODE) { // to disable

//        driving_bcr_ = make_unique<BcrBL185>("BCR1", RF2217_ECAT_SLAVE_PORT + 15, "/dev/tty5", 38400);
//       trans_bcr_ = make_unique<BcrBL185>("BCR2", RF2217_ECAT_SLAVE_PORT + 16, "/dev/tty7", 38400);
    } else {
//        driving_bcr_ = make_unique<BcrBL185>("BCR1", RF2217_ECAT_SLAVE_PORT + 15, "/dev/ttyS5", 38400);
//        trans_bcr_ = make_unique<BcrBL185>("BCR2", RF2217_ECAT_SLAVE_PORT + 16, "/dev/ttyS7", 38400);
    }
    return true;
}

bool OHTController::initializeServo()
{
    front_wheel_ = make_unique<SingleAxisAmcDrive>("front_wheel", ServoOpMode::CSP);
    rear_wheel_ = make_unique<SingleAxisAmcDriveRear>("rear_wheel", ServoOpMode::CSV);  // 210119 yjchoe
    hoist_ = make_unique<SingleAxisAmcDrive>("hoist", ServoOpMode::CSP);
    slide_ = make_unique<SingleAxisAmcDrive>("slide", ServoOpMode::CSP);

    // set units
    hoist_->setUserUnit("pulse", 1); //1<<7
    front_wheel_->setUserUnit("pulse", 1);
    rear_wheel_->setUserUnit("pulse", 1);
    slide_->setUserUnit("pulse", 1);

    // initialization -
    sleep_for(30ms);    // 210730 yjchoe, for clear servo alarm(min:20ms)
    SEP_REQUIRE_RET(front_wheel_->initialize(0, "SGM7P-04AFA21", 1, true), false); // \note enable-on-init is disabled!
    SEP_REQUIRE_RET(rear_wheel_->initialize(1, "SGM7P-04AFA2C", 1, true), false);
    SEP_REQUIRE_RET(hoist_->initialize(2, "SGM7J-02A7A2C", 1, true), false);
    SEP_REQUIRE_RET(slide_->initialize(3, "SGM7J-01A7A21", 1, true), false);
    axes_ = {front_wheel_.get(), rear_wheel_.get(), hoist_.get(), slide_.get()};
    motors_ = {axes_[0]->getMotor(0), axes_[1]->getMotor(0), axes_[2]->getMotor(0),axes_[3]->getMotor(0)};

//    front_wheel_->resetEncoder(true);
//    rear_wheel_->resetEncoder(true);

//    front_wheel_->setMaxTrq(300);
//    rear_wheel_->setMaxTrq(300);
    //hoist_->setMaxTrq(300);
    //slide_->setMaxTrq(300);

    // set limits
    for(int i = 0; i < 4; i++) {
        double max_v = config_.v_max[i]*1.05;   // amc limit < motion limit
        double min_a = config_.a_max[i].first;
        double max_a = config_.a_max[i].second;
        double min_p = config_.pos_limit[i].first;
        double max_p = config_.pos_limit[i].second;
        axes_[i]->setMax(max_v, max_a, min_a);      // vel, max, min
        if (config_.pos_limit[i].first != config_.pos_limit[i].second) {
            axes_[i]->setPosLimit( min_p, max_p);
        }
        else {
            axes_[i]->setPosLimitPulse(axes_[i]->getPosLimitPulse().first, axes_[i]->getPosLimitPulse().second);
        }
    }

    // set immediate motion change mode
    for(auto axis : axes_) {
        axis->setMotionChangeMode(true);
    }

    // release hoist tolerance
    motors_[OHT_HOIST]->setOverloadCondition(100, 128*320*2, 128*10);

    // set rear wheel as slave of front wheel
    rear_wheel_->follow(front_wheel_->getMotor(0)->getSlaveId());
    return true;
}

bool OHTController::makeBehaviors()
{
    if (AMC_CLIENT_MODE) {
        perceptions_.emplace_back(std::make_unique<MarkUpdater>(this));
        localizer_ = (Localization *) perceptions_.front().get();
    } else {
        perceptions_.emplace_back(std::make_unique<Localization>(this));
        localizer_ = (Localization *) perceptions_.front().get();
        //perceptions_.emplace_back(std::make_unique<Monitoring>(this));
        //perceptions_.emplace_back(std::make_unique<ObstacleDectection>(this));
        //drive_ = std::make_unique<Driving>(this, *localizer_);
        //trans_ = std::make_unique<Transfer>(this);
    }

    return true;
}

bool OHTController::startService()
{
    if (AMC_CLIENT_MODE) {
        //return localizer_->runAsThread(); --> Amc4OHT::statusUpdate_thread()에서 호출
        return true;
    }
    else {
        //trans_->runAsThread(1000); 
        if (thread_ == nullptr) {
            thread_ = std::make_unique<std::thread>([this]() { this->svc(); });
        }
        return thread_ != nullptr;
    }
}

void OHTController::stopService()
{
    if (AMC_CLIENT_MODE) {
        localizer_->stopThread();
    }
    if (thread_ != nullptr) {
        thread_exit_ = true;
        thread_->join();
        thread_.reset(nullptr);
        //trans_->stopThread();
    }
}

void OHTController::svc()
{
    auto wake_up_at = std::chrono::system_clock::now();
    auto duration = std::chrono::nanoseconds(ECAT_EMULATION_CYCLETIME_NS*1);
    setCpuAffinity(2);
    addIntoRealtimeSchedule(USER_RT_APP_PRIO);
    while(!thread_exit_) {
        //SEP_LOGE("start");
        wake_up_at += duration;
        lock_.lock();
        for(auto& bh: perceptions_) {
            bh->update();
        }
        //drive_->update();

        if (wake_up_at < std::chrono::system_clock::now()) {
            SEP_LOGE("Time Quantum exhaust!... %.2fms\n", (std::chrono::system_clock::now() - wake_up_at).count()/1e6);
            wake_up_at = std::chrono::system_clock::now();
        }
        lock_.unlock();
        std::this_thread::sleep_until(wake_up_at);
    }
}
template<typename T>
T getParamSafe(const YAML::Node& node, size_t index, T defaultValue, T minVal, T maxVal, const std::string& parentPath = "")
{
	try {
		// 1. 필드가 없는 경우
		if (!node.IsDefined() || !node.IsSequence() || index >= node.size()) {
			SEP_LOGT("[ParamLoader] Warning: Index '%zu' field is missing in '%s'. Using default: %s", 
				index, parentPath.c_str(), std::to_string(defaultValue).c_str());
			return defaultValue;
		}

		// 2. 값 읽기 및 변환
		T value = node[index].as<T>();

		// 3. 범위 검사 (Min ~ Max)
		if (value < minVal || value > maxVal) {
			SEP_LOGT("[ParamLoader] Warning: Index '%zu' value(%s) is out of range [%s ~ %s] in '%s'. Using default: %s", 
				index, 
				std::to_string(value).c_str(), 
				std::to_string(minVal).c_str(), 
				std::to_string(maxVal).c_str(),
				parentPath.c_str(),
				std::to_string(defaultValue).c_str()
			);
			return defaultValue;
		}
		SEP_LOGT("[AMC ParamLoader] Index[%zu] %s value : (%s)", index, parentPath.c_str(), std::to_string(value).c_str());

		return value;

	} catch (const std::exception& e) {
		// 4. 타입 변환 실패 등 예외 발생
		SEP_LOGT("[ParamLoader] Warning: Failed to read '%zu'. Reason: %s. Using default: %s", 
			index, e.what(), std::to_string(defaultValue).c_str());
		return defaultValue;
	}
}

bool OHTController::loadConfig(const char* _dir_prefix)
{
    if (initialized_) return true;

    string model_file = "amc_param.yml";
    try {
        // 1. 파일 로드
        auto root = YAML::LoadFile(model_file);
        
        // 2. 경로 유효성 확인 (OHT -> Limits)
        if (!root["OHT"] || !root["OHT"]["Limits"]) {
            SEP_LOGT("!---- Critical Error: 'OHT' or 'Limits' structure is missing. Using default config. ----!");
            // 구조가 없으면 기본값으로 초기화를 시도하거나 false를 리턴해야 합니다.
            // 여기서는 안전을 위해 false 리턴 예시 (혹은 기본값 세팅 로직 추가 가능)
            // return false; 
            // 사용자 요청에 따라 기본값을 쓰려면 아래 로직을 타게둬야 하므로 예외 처리에 맡깁니다.
            throw YAML::InvalidNode();
        }
        auto config = root["OHT"]["Limits"];

        // 3. Limits 데이터 로딩
        // [주의] 아래 기본값과 범위는 장비 사양에 맞게 수정해주세요.
        for(int i : {OHT_WHEEL_F, OHT_HOIST, OHT_SLIDE}) {
            int cfg_index = (i==OHT_WHEEL_F)? i : i-1;
            //std::string cfg_index = std::to_string(cfg_index_num);
            
            // 경로 문자열 생성 (로그용)
            std::string path_pos_min = "pos/min";
            std::string path_pos_max = "pos/max";
            // ... 나머지 경로들 ...

            // pos_limit (min, max)
            // 예: 기본값 -10000/10000, 범위 -50000 ~ 50000
            config_.pos_limit[i].first  = getParamSafe<double>(config["pos"]["min"], cfg_index, -10000.0, -50000.0, 50000.0, "pos/min");
            config_.pos_limit[i].second = getParamSafe<double>(config["pos"]["max"], cfg_index, 10000.0, -50000.0, 50000.0, "pos/max");

            // v_max
            config_.v_max[i] = getParamSafe<double>(config["vel"], cfg_index, 1000.0, 0.0, 10000.0, "vel");
            
            // a_max (min, max)
            config_.a_max[i].first  = getParamSafe<double>(config["acc"]["min"], cfg_index, 500.0, 0.0, 15000.0, "acc/min");
            config_.a_max[i].second = getParamSafe<double>(config["acc"]["max"], cfg_index, 500.0, 0.0, 15000.0, "acc/max");
            
            // inpos
            config_.inpos[i] = getParamSafe<double>(config["pos"]["inpos"], cfg_index, 5.0, 0.0, 100.0, "pos/inpos");
            
            // f_err, f_rawerr
            config_.f_err[i]     = getParamSafe<double>(config["Err"], cfg_index, 10000.0, 0.0, 1000000.0, "Err");
            config_.f_rawerr[i]  = getParamSafe<double>(config["RawErr"], cfg_index, 50000.0, 0.0, 1000000.0, "RawErr");
        }

        // WHEEL_R 설정 (WHEEL_F 값 복사)
        config_.pos_limit[OHT_WHEEL_R] = config_.pos_limit[OHT_WHEEL_F];
        config_.pos_margin[OHT_WHEEL_R] = config_.pos_margin[OHT_WHEEL_F];
        config_.inpos[OHT_WHEEL_R] = config_.inpos[OHT_WHEEL_F];
        config_.v_max[OHT_WHEEL_R] = config_.v_max[OHT_WHEEL_F];
        config_.a_max[OHT_WHEEL_R] = config_.a_max[OHT_WHEEL_F];
        config_.f_err[OHT_WHEEL_R] = config_.f_err[OHT_WHEEL_F];
        config_.f_rawerr[OHT_WHEEL_R] = config_.f_rawerr[OHT_WHEEL_F];
        
        initialized_ = true;
        return true;

    } catch (const YAML::Exception& e) {
        SEP_LOGT("!---- YAML Error in loadConfig: %s ----!", e.what());
        sleep_for(100ms);
        // 에러 발생 시 초기화되지 않음을 명시하거나 기본값 상태로 둠
        initialized_ = false; 
        return false;
    } catch (...) {
        SEP_LOGT("!---- Unknown Error in loadConfig ----!");
        initialized_ = false;
        return false;
    }
}

bool OHTController::loadIOConfig() {
    SEP_LOGT("[I/O Config] Load I/O Config!");
    loadIOStatus = true;

    // Map 초기화
    ioConfig_.clear();
    bit_inversion_ports_.clear();
    active_low_ports_.clear();

    try {
        string model_file = "amc_param.yml";
        auto config_file = YAML::LoadFile(model_file)["OHT"]["IO_Map"];

        for (auto IO_name : {"Input", "Output", "Etc"}) {
            auto IOMap = config_file[IO_name];
            if (IOMap.size() != 0) {
                //printf("[%s I/O]\n", IO_name);
                SEP_LOGT("[I/O Config] %s I/O", IO_name);
                int IO_size = 0;
                for (int i = 0; i < IOMap.size(); ++i) {
                    auto Num = IOMap[i]["Num"].as<int>();
                    auto Name = IOMap[i]["Name"].as<string>();
                    auto Board = IOMap[i]["Board"].as<int>();
                    auto Port = IOMap[i]["Port"].as<int>();
                    auto Bit = IOMap[i]["Bit"].as<int>();
                    auto Portid = makePortId(Board, Port, Bit);

                    IO_size = Num;
                    if (IO_name == "Input")           Num = Num;
                    else if (IO_name == "Output")     Num += OutOffset;
                    else if (IO_name == "Etc")        Num += EtcOffset;
                    ioConfig_.setportid(Name, Num, Portid);
                    //printf("IO[%d] Name[%s] Board[%d] Port[%d] Bit[%d] MakrportID[%d]\n", Num, Name.c_str(), Board, Port, Bit, ioConfig_.getportid(Num));

                    // Map에 Portid가 제대로 들어갔는지 확인하여 다시 입력
                    for (int j = 0; j < 3; j++) {
                        if (Portid != ioConfig_.getportid(Num)
                        || Portid != ioConfig_.getportid(Name)
                        || Name != ioConfig_.getportstring(Portid)) {
                            Portid = makePortId(Board, Port, Bit);
                            ioConfig_.modifyportid(Name, Num, Portid);
                            SEP_LOGT("Retry %d, I/O[%d] Name[%s] MakeportID[%d]", j+1, Num, Name.c_str(), Portid);
                        }
                    }
                    SEP_LOGT("I/O[%d] Name[%s] Board[%d] Port[%d] Bit[%d] MakeportID[%d]", Num, Name.c_str(), Board, Port, Bit, ioConfig_.getportid(Num));
                }
                ioConfig_.setportsize(IO_name, IO_size);
                //printf("%s %d\n", IO_name, ioConfig_.getportsize(IO_name));
            }
        }
        bit_inversion_ports_ = ioConfig_.get_inversion_port();
        active_low_ports_ = ioConfig_.get_active_low_port();
    } catch (std::exception &e) {
        SEP_TRE("IO Config Loading Error : %s\n", e.what());
        return false;
    }
    loadIOStatus = false;
    return true;
}

unsigned OhtIoConfig::getportid(string name)
{
    // name에 해당하는 key가 없는 경우 0을 반환
    if (name_map.find(name) != name_map.end())
        return num_map[name_map[name]];
    else
        return 0;
}

unsigned OhtIoConfig::getportid(unsigned num)
{
    // num에 해당하는 key가 없는 경우 0을 반환
    if (num_map.find(num) != num_map.end())
        return num_map[num];
    else
        return 0;
}

string OhtIoConfig::getportstring(unsigned portid)
{
    if (portid_map.find(portid) != portid_map.end())
        return portid_map[portid];
    else
        return "";
}

void OhtIoConfig::setportid(string name, unsigned num, unsigned portid)
{
    // port map
    name_map.insert(std::make_pair(name, num));
    num_map.insert(std::make_pair(num, portid));
    portid_map.insert(std::make_pair(portid, name));

    // inversion port map
    for (int i = 0; i < sizeof(bit_inversion_str)/sizeof(string); ++i) {
        if(name == bit_inversion_str[i]) {
            inversion_ports_.insert(portid);
            break;
        }
    }
    for (int i = 0; i < sizeof(active_low_str)/sizeof(string); ++i) {
        if(name == active_low_str[i]) {
            low_ports_.insert(portid);
            break;
        }
    }

    // io val map
    io_val_map.insert(std::make_pair(portid, false));
}

void OhtIoConfig::modifyportid(string name, unsigned num, unsigned portid)
{
    // 기존 요소 삭제
    name_map.erase(name);
    num_map.erase(num);
    portid_map.erase(portid);
    inversion_ports_.erase(portid);
    low_ports_.erase(portid);
    io_val_map.erase(portid);

    // 요소 추가
    setportid(name, num, portid);
}

void OhtIoConfig::clear()
{
    name_map.clear();
    num_map.clear();
    portid_map.clear();
    inversion_ports_.clear();
    low_ports_.clear();
    io_val_map.clear();
    size_map.clear();
}

std::set<unsigned int> OhtIoConfig::get_inversion_port()
{
    return inversion_ports_;
}

std::set<unsigned int> OhtIoConfig::get_active_low_port()
{
    return low_ports_;
}

int OhtIoConfig::getportsize(string key)
{
    if (size_map.find(key) != size_map.end())
        return size_map[key];
    else
        return 0;
}

void OhtIoConfig::setportsize(string io_name, int size)
{
    size_map.insert(std::make_pair(io_name, size));
}
