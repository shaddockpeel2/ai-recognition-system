"""
算法模块统一入口
"""
from .helmet_detection import HelmetDetector
from .vest_detection import VestDetector
from .fall_detection import FallDetector
from .mask_detection import MaskDetector
from .person_intrusion import PersonIntrusionDetector
from .smoke_detection import SmokeDetector
from .fire_detection import FireDetector
from .crowd_gathering import CrowdGatheringDetector
from .person_absence import PersonAbsenceDetector
from .vehicle_parking import VehicleParkingDetector
from .fire_extinguisher_absence import FireExtinguisherAbsenceDetector
from .reagent_bottle_detection import ReagentBottleDetector
from .camera_occlusion_detection import CameraOcclusionDetector
from .fight_detection import FightDetector
from .glove_detection import GloveDetector
from .crack_detection import CrackDetector
from .dog_detection import DogDetector
from .cat_detection import CatDetector
from .fatigue_driving_detection import FatigueDrivingDetector
from .trash_detection import TrashDetector
from .electric_bicycle_detection import ElectricBicycleDetector
from .theft_detection import TheftDetector
from .work_clothes_detection import WorkClothesDetector
from .safehat_vest_detection import SafehatVestDetector
from .gas_cylinder_tilt_detection import GasCylinderTiltDetector
from .box_object_detection import BoxObjectDetector

__all__ = ["HelmetDetector", "VestDetector", "FallDetector", "MaskDetector", "PersonIntrusionDetector", "SmokeDetector", "FireDetector", "CrowdGatheringDetector", "PersonAbsenceDetector", "VehicleParkingDetector", "FireExtinguisherAbsenceDetector", "ReagentBottleDetector", "CameraOcclusionDetector", "FightDetector", "GloveDetector", "CrackDetector", "DogDetector", "CatDetector", "FatigueDrivingDetector", "TrashDetector", "ElectricBicycleDetector", "TheftDetector", "WorkClothesDetector", "SafehatVestDetector", "GasCylinderTiltDetector", "BoxObjectDetector"]
