package com.suyiiyii.live_record

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/** The foreground notification says what the link and the upload are doing (10-06: it never changed), in the phone's
 *  language: getString on the strings in res/values-zh (Chinese) and res/values (any other language). */
class NoticeTest {
    private val now = 1_800_000_000_000L
    private val hw = mapOf("battery" to 68, "store" to 0)
    private val names = R.string::class.java.fields.associate { it.getInt(null) to it.name }
    private fun strings(dir: String): (Int, Array<out Any?>) -> String {
        val n = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(File("src/main/res/$dir/strings.xml")).getElementsByTagName("string")
        val s = (0 until n.length).associate { n.item(it).attributes.getNamedItem("name").nodeValue to n.item(it).textContent.replace("\\'", "'") }
        return { id, a -> String.format(s[names[id]]!!, *a) }
    }
    private val zh = strings("values-zh")
    private val en = strings("values")
    private fun ble(vararg kv: Pair<String, Any?>, str: (Int, Array<out Any?>) -> String = zh) =
        RecorderService.notice(mapOf("running" to true, "source" to "ble", "pending" to 0) + kv, now, str)
    private val live = arrayOf("link" to say("connected"), "connected" to true)

    @Test fun link() {
        assertEquals("正在找 LR-X1-567E" to "", ble("link" to say("scanning", "name" to "LR-X1-567E"), "hw" to hw))  // the battery read before is stale
        assertEquals("已连接 · 实时上传" to "电量 68%", ble(*live, "hw" to hw, "pending" to 1, "oldest" to now - 25_000))
        // at connect only the file being recorded holds back LIVE, not the backlog (old firmware: up to 16 MiB; newer firmware: none)
        assertEquals("接上实时前先取正在录的这段 · 还差 1.2 MB" to "电量 68%",
            ble("link" to say("joining"), "hw" to hw, "syncDone" to 512L, "syncTotal" to 45568L, "syncLive" to 1228L))
        assertEquals("正在接上实时" to "", ble("link" to say("joining"), "hw" to hw))
        assertEquals("实时上传 · 后台补传 1.5 / 44.5 MB" to "电量 68%",  // a backlog no longer holds back what is said now
            ble(*live, "hw" to hw, "syncDone" to 1536L, "syncTotal" to 45568L))
        assertEquals("已连接 · 实时上传" to "上传失败 HTTP 502 · 已暂停，还剩 15 分钟 · 电量 9% · 存储卡已满 · 待传 40 块",
            ble(*live, "note" to say("uploadFailed", "detail" to "HTTP 502"), "pausedUntil" to now + 14 * 60_000 + 1,
                "hw" to mapOf("battery" to 9, "store" to -28), "pending" to 40, "oldest" to now - 300_000))
        assertEquals("扫描失败 2，5 秒后重试" to "", ble("link" to say("scanFailed", "err" to 2, "s" to 5L)))
        assertEquals("录音设备出错：GATT 133" to "", ble("link" to say("linkError", "detail" to "GATT 133")))
        assertEquals("正在启动" to "", ble())
        // a bean's backlog: it is not recording, nothing goes up live
        assertEquals("补传录音豆里的录音 1.5 / 44.5 MB" to "电量 68%",
            ble("link" to say("beanIdle"), "connected" to true, "hw" to hw, "syncDone" to 1536L, "syncTotal" to 45568L))
        assertEquals("录音豆已连上 · 没在录音" to "电量 68%", ble("link" to say("beanIdle"), "connected" to true, "hw" to hw))
    }

    @Test fun english() {
        assertEquals("Connected · live upload" to "Upload failed: HTTP 502 · Paused, 15 min left · Battery 9% · Card full · 40 pieces to upload",
            ble(*live, "note" to say("uploadFailed", "detail" to "HTTP 502"), "pausedUntil" to now + 14 * 60_000 + 1,
                "hw" to mapOf("battery" to 9, "store" to -28), "pending" to 40, "oldest" to now - 300_000, str = en))
        assertEquals("Live upload · catching up 1.5 / 44.5 MB" to "Battery 68% · Charging",
            ble(*live, "hw" to hw + ("charge" to "charging"), "syncDone" to 1536L, "syncTotal" to 45568L, str = en))
        assertEquals("Reading the recorder's status" to "", ble("link" to say("status"), str = en))
        assertEquals("alldayrec is uploading · 7 pieces left" to "The server could not decode a piece: dropped",
            RecorderService.notice(mapOf("running" to false, "pending" to 7, "note" to say("rejected")), now, en))
    }

    @Test fun everyCode() {  // each code the link and the uploader say has its words in both languages
        val codes = listOf("scanning", "bluetoothOff", "unbound", "kicked", "elsewhere", "bluetoothNotOn", "scanFailed", "connectFailed",
            "away", "connecting", "mtu", "pairing", "pairingFailed", "services", "clock", "status", "forgetOthers", "joining", "connected",
            "encrypting", "beanIdle", "linkError", "rejected", "uploadFailed", "endFailed", "archiveFailed")
        for (str in listOf(zh, en)) for (c in codes) assertNotEquals(c, RecorderService.said(say(c, "lastSeen" to now), str))
        assertEquals("录音设备不在附近，它在离线录音，回来后自动补传", RecorderService.said(say("away", "lastSeen" to null), zh))
    }

    @Test fun card() {  // an errno, never shown as one: no card (LR-P3-2420, 10-07: -116 mounting, total 0) or a failed one
        assertEquals("已连接 · 实时上传" to "电量 97% · 没插存储卡",
            ble(*live, "hw" to mapOf("battery" to 97, "store" to -116, "total_kb" to 0L)))
        assertEquals("电量 97% · 没插存储卡", ble(*live, "hw" to mapOf("battery" to 97, "store" to -19, "total_kb" to 0L)).second)
        assertEquals("电量 97% · 存储卡出错", ble(*live, "hw" to mapOf("battery" to 97, "store" to -5, "total_kb" to 31_000_000L)).second)
        assertEquals("电量 97% · 存储卡在 U 盘模式", ble(*live, "hw" to mapOf("battery" to 97, "store" to -16, "total_kb" to 31_000_000L)).second)
    }

    @Test fun charge() {  // the status read's byte 47 (firmware lr_power.h), and the line it makes
        assertEquals(listOf(null, "battery", "charging", "full", null),
            listOf(ByteArray(47), ByteArray(48), ByteArray(48).also { it[47] = 1 }, ByteArray(48).also { it[47] = 2 }, ByteArray(48).also { it[47] = 7 }).map(Ble::charge))
        assertEquals("已连接 · 实时上传" to "电量 68% · 充电中", ble(*live, "hw" to hw + ("charge" to "charging")))
    }

    @Test fun micAndUpload() {
        assertEquals("alldayrec 正在录音" to "", RecorderService.notice(mapOf("running" to true, "source" to "mic", "pending" to 0), now, zh))
        assertEquals("alldayrec 正在上传 · 剩 7 块" to "", RecorderService.notice(mapOf("running" to false, "pending" to 7), now, zh))
    }
}
