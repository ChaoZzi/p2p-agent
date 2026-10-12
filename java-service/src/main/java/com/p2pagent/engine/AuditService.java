package com.p2pagent.engine;

import com.p2pagent.api.dto.AuditView;
import com.p2pagent.db.AuditRepository;
import com.p2pagent.mockoa.web.TraceFilter;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

// 一个类没写构造方法时，编译器只会自动生成一个"无参构造"，不会生成"全参构造"。

/**
 * ① 扫描到 AuditService（有 @Service/@Component 之类）
 *         ↓
 * ② 决定要创建它，先找构造方法
 *    → 你没写构造 → 用编译器默认生成的无参构造：AuditService()
 *         ↓
 * ③ 调用无参构造，创建对象
 *    → new AuditService()
 *    → 此刻 auditRepository == null  ❗️（对象已存在，依赖还没注入）
 *         ↓
 * ④ 对象已存在，进入「属性填充/依赖注入」阶段
 *    → Spring 用反射找到 @Autowired 标注的字段 auditRepository
 *    → 去容器里找 AuditRepository 类型的 Bean
 *         ↓
 * ⑤ 找到 AuditRepository 实例（前面已创建好，或此刻创建）
 *         ↓
 * ⑥ 用反射 field.set(service, auditRepository实例)
 *    → auditRepository 终于有值了 ✅
 *         ↓
 * ⑦ Bean 创建完成，放入单例池
 *         ↓
 * ⑧ 别处 @Autowired AuditService 时，拿到这个完整对象
 */
@Service
public class AuditService {
    /**
     * 字段注入不能finial的原因
     * 1、finial 描述的字段 要求在构造函数结束之前就必须被赋值
     * 2、创建bean实例时 是先通过构造函数创建对象  然后才依赖注入 (这个依赖是@autowired修饰的 以及setter方法
     * 构造器注入例外  他是创建对象和依赖注入同时发生的)
     * 3、所以此时 finial所描述的字段已经确定值是null 后面就不能再修改了 所以依赖就无法成功传递
     */

    /**
     * spring每次启动时都会创建重新扫描并创建bean实例
     * 默认单例 也就是一个类只对应一个实例
     * 构造器注入 相比于 字段注入  的优势就是字段不可变
     */
    @Autowired
    private AuditRepository auditRepository;

    public void append(String flowId, String actor, AuditAction action, String detail){
        String traceId = TraceFilter.currentTraceId();
        String auditId = UUID.randomUUID().toString();
        String at = Instant.now().toString();
        String actionName = action.name();
        AuditView auditView = new AuditView(auditId, flowId, actor, actionName, detail,traceId, at);
        auditRepository.insert(auditView);

    }
    public List<AuditView> listByFlow(String flowId){
        List<AuditView> auditViews = auditRepository.listByFlow(flowId);
        return auditViews;
    }  // 按 at 升序
}