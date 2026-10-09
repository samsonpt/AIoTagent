# IPC 工艺要点摘要（PCB 制造）

## 适用范围
本摘要覆盖钻孔、电镀、蚀刻关键工序的 IPC 对齐要点，供云端 RCA 与维护决策参考。

## 钻孔（drill）
- 孔壁粗糙度应满足产品等级要求，异常磨损需 **change_bit**
- 孔径公差按 IPC-6012 相应等级控制

## 电镀（plating）
- 厚度分布与光泽为关键质量属性
- 添加剂耗尽（additive_depletion）时 **dose_additive**
- 整流器异常（rectifier_low）时 **repair_rectifier**

## 蚀刻（etch）
- 线宽与残铜为 AOI 主要监控项
- 喷嘴堵塞（nozzle_clog）时 **clean_nozzle**
- 比重漂移（etch_sg_drift）时 **adjust_sg**

## 追溯要求
- 批次履历须关联工序参数、维护动作与 AOI 结果
- 重大异常启动 8D 闭环
