package com.suyiiyii.live_record

import org.junit.Assert.assertEquals
import org.junit.Test

/** The service's 2 s thread (#423 review L4): a fast transfer's wait for BLE that throws leaves the notification's
 *  refresh to run, and the other way round. */
class EachAloneTest {
    @Test fun oneThrowingLeavesTheOthers() {
        val ran = mutableListOf<String>()
        eachAlone({ throw IllegalStateException("wifiWait") }, { ran += "refresh" })
        eachAlone({ ran += "wifiWait" }, { throw IllegalStateException("refresh") }, { ran += "after" })
        assertEquals(listOf("refresh", "wifiWait", "after"), ran)
    }
}
