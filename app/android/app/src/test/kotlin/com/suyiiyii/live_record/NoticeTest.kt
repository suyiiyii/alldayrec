package com.suyiiyii.live_record

import org.junit.Assert.assertEquals
import org.junit.Test

/** The foreground notification says what the link and the upload are doing (10-06: it never changed). */
class NoticeTest {
    private val now = 1_800_000_000_000L
    private val hw = mapOf("battery" to 68, "store" to 0)
    private fun ble(vararg kv: Pair<String, Any?>) = RecorderService.notice(mapOf("running" to true, "source" to "ble", "pending" to 0) + kv, now)

    @Test fun link() {
        assertEquals("正在找 LR-X1-567E" to "", ble("link" to "正在找 LR-X1-567E", "hw" to hw))  // the battery read before is stale
        assertEquals("已连接 · 实时上传" to "电量 68%", ble("link" to "已连接 · 实时上传", "hw" to hw, "pending" to 1, "oldest" to now - 25_000))
        // at connect only the file being recorded holds back LIVE, not the backlog (old firmware: up to 16 MiB; newer firmware: none)
        assertEquals("接上实时前先取正在录的这段 · 还差 1.2 MB" to "电量 68%",
            ble("link" to "正在接上实时", "hw" to hw, "syncDone" to 512L, "syncTotal" to 45568L, "syncLive" to 1228L))
        assertEquals("正在接上实时" to "", ble("link" to "正在接上实时", "hw" to hw))
        assertEquals("实时上传 · 后台补传 1.5 / 44.5 MB" to "电量 68%",  // a backlog no longer holds back what is said now
            ble("link" to "已连接 · 实时上传", "hw" to hw, "syncDone" to 1536L, "syncTotal" to 45568L))
        assertEquals("已连接 · 实时上传" to "上传失败 HTTP 502 · 已暂停，还剩 15 分钟 · 电量 9% · 存储卡已满 · 待传 40 块",
            ble("link" to "已连接 · 实时上传", "note" to "上传失败 HTTP 502", "pausedUntil" to now + 14 * 60_000 + 1,
                "hw" to mapOf("battery" to 9, "store" to -28), "pending" to 40, "oldest" to now - 300_000))
    }

    @Test fun card() {  // an errno, never shown as one: no card (LR-P3-2420, 10-07: -116 mounting, total 0) or a failed one
        assertEquals("已连接 · 实时上传" to "电量 97% · 没插存储卡",
            ble("link" to "已连接 · 实时上传", "hw" to mapOf("battery" to 97, "store" to -116, "total_kb" to 0L)))
        assertEquals("电量 97% · 没插存储卡", ble("link" to "已连接 · 实时上传", "hw" to mapOf("battery" to 97, "store" to -19, "total_kb" to 0L)).second)
        assertEquals("电量 97% · 存储卡出错", ble("link" to "已连接 · 实时上传", "hw" to mapOf("battery" to 97, "store" to -5, "total_kb" to 31_000_000L)).second)
        assertEquals("电量 97% · 存储卡在 U 盘模式", ble("link" to "已连接 · 实时上传", "hw" to mapOf("battery" to 97, "store" to -16, "total_kb" to 31_000_000L)).second)
    }

    @Test fun charge() {  // the status read's byte 47 (firmware lr_power.h), and the line it makes
        assertEquals(listOf(null, "battery", "charging", "full", null),
            listOf(ByteArray(47), ByteArray(48), ByteArray(48).also { it[47] = 1 }, ByteArray(48).also { it[47] = 2 }, ByteArray(48).also { it[47] = 7 }).map(Ble::charge))
        assertEquals("已连接 · 实时上传" to "电量 68% · 充电中", ble("link" to "已连接 · 实时上传", "hw" to hw + ("charge" to "charging")))
    }

    @Test fun micAndUpload() {
        assertEquals("alldayrec 正在录音" to "", RecorderService.notice(mapOf("running" to true, "source" to "mic", "pending" to 0), now))
        assertEquals("alldayrec 正在上传 · 剩 7 块" to "", RecorderService.notice(mapOf("running" to false, "pending" to 7), now))
    }
}
