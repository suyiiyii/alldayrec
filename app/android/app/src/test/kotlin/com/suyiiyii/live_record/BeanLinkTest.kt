package com.suyiiyii.live_record

import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/** Drive the same waiter as the PendingIntent receiver, without Android or a real bean. */
class BeanLinkTest {
    private val name = "D3200-ABCD"

    @Test fun waitTimesOutAndReregistersImmediately() {
        val registrations = LinkedBlockingQueue<Int>()
        val stops = AtomicInteger()
        val starts = AtomicInteger()
        val finished = CompletableFuture<String?>()
        val scan = BeanScanWait<String>(name, { registrations.put(starts.incrementAndGet()) }, { stops.incrementAndGet() })
        val worker = Thread {
            try {
                var first = true
                scan.loop({ !finished.isDone }, {
                    if (first) { assertNull(scan.await(30)); first = false }
                    else finished.complete(scan.await())
                    true
                }, { throw AssertionError(it) }, {})
            } catch (e: Throwable) { finished.completeExceptionally(e) }
        }.apply { start() }
        try {
            assertEquals(1, registrations.poll(2, TimeUnit.SECONDS))
            assertEquals(2, registrations.poll(2, TimeUnit.SECONDS))
            assertEquals(1, stops.get())  // the replacement stays registered until delivery
            assertTrue(scan.deliver(name, true, "renewed registration"))
            assertEquals("renewed registration", finished.get(2, TimeUnit.SECONDS))
            assertEquals(2, stops.get())
        } finally { worker.interrupt(); worker.join(2000) }
    }

    @Test fun fastConnectFailureIgnoresResultsForFiveSecondsThenConnectsOnce() {
        val now = AtomicLong(10_000)
        val registrations = LinkedBlockingQueue<Int>()
        val starts = AtomicInteger()
        val stops = AtomicInteger()
        val scan = BeanScanWait<String>(name, { registrations.put(starts.incrementAndGet()) }, { stops.incrementAndGet() }, now::get)
        val attempts = AtomicInteger()
        val logs = AtomicInteger()
        val finished = CompletableFuture<Unit>()
        val worker = Thread {
            try {
                scan.loop({ attempts.get() < 2 }, {
                    scan.await() ?: error("unexpected timeout")
                    if (attempts.incrementAndGet() == 1) throw IOException("connect failed before link up")
                    true
                }, { true }, { logs.incrementAndGet() })
                finished.complete(Unit)
            } catch (e: Throwable) { finished.completeExceptionally(e) }
        }.apply { start() }
        try {
            assertEquals(1, registrations.poll(2, TimeUnit.SECONDS))
            assertTrue(scan.deliver(name, true, "first attempt"))
            assertEquals(2, registrations.poll(2, TimeUnit.SECONDS))
            now.set(14_999)
            assertFalse(scan.deliver(name, true, "too soon"))
            assertEquals(1, stops.get())
            assertEquals(1, attempts.get())
            now.set(15_000)
            assertTrue(scan.deliver(name, true, "retry"))
            assertFalse(scan.deliver(name, true, "duplicate"))
            finished.get(2, TimeUnit.SECONDS)
            assertEquals(2, attempts.get())
            assertEquals(1, logs.get())
            assertFalse(scan.backoff())  // another failure in this streak does not log again
            scan.connected()
            assertTrue(scan.backoff())  // a new streak logs once again
        } finally { worker.interrupt(); worker.join(2000) }
    }

    @Test fun scanErrorWaitsFiveSecondsBeforeRegisteringReplacement() {
        val now = AtomicLong(20_000)
        val registrations = LinkedBlockingQueue<Int>()
        val starts = AtomicInteger()
        val stops = AtomicInteger()
        val scan = BeanScanWait<String>(name, { registrations.put(starts.incrementAndGet()) }, { stops.incrementAndGet() }, now::get)
        val finished = CompletableFuture<String?>()
        val worker = Thread {
            try {
                scan.loop({ !finished.isDone }, { finished.complete(scan.await()); true }, { true }, {})
            } catch (e: Throwable) { finished.completeExceptionally(e) }
        }.apply { start() }
        try {
            assertEquals(1, registrations.poll(2, TimeUnit.SECONDS))
            val failedAt = System.nanoTime()
            scan.fail(IOException("scan failed"))
            assertNull(registrations.poll(1, TimeUnit.SECONDS))
            assertFalse(scan.deliver(name, true, "during backoff"))
            now.set(25_001)
            assertEquals(2, registrations.poll(5, TimeUnit.SECONDS))
            assertTrue(System.nanoTime() - failedAt >= TimeUnit.SECONDS.toNanos(5))
            assertEquals(1, stops.get())
            assertTrue(scan.deliver(name, true, "after backoff"))
            assertEquals("after backoff", finished.get(2, TimeUnit.SECONDS))
            assertEquals(2, stops.get())
        } finally { worker.interrupt(); worker.join(2000) }
    }

    @Test fun bluetoothOffFailureDoesNotAddScanErrorDelay() {
        val registrations = LinkedBlockingQueue<Int>()
        val starts = AtomicInteger()
        val now = AtomicLong(10_000)
        val finished = CompletableFuture<String?>()
        val scan = BeanScanWait<String>(name, { registrations.put(starts.incrementAndGet()) }, {}, now::get)
        val worker = Thread {
            try {
                scan.loop({ !finished.isDone }, { finished.complete(scan.await()); true }, { true }, {})
            } catch (e: Throwable) { finished.completeExceptionally(e) }
        }.apply { start() }
        try {
            assertEquals(1, registrations.poll(2, TimeUnit.SECONDS))
            scan.fail(IOException("Bluetooth off"), scanError = false)
            assertEquals(2, registrations.poll(1, TimeUnit.SECONDS))
            now.set(15_000)
            assertTrue(scan.deliver(name, true, "Bluetooth back on"))
            assertEquals("Bluetooth back on", finished.get(2, TimeUnit.SECONDS))
        } finally { worker.interrupt(); worker.join(2000) }
    }

    @Test fun repeatedRegistrationFailureWaitsAndStopInterruptsPromptly() {
        val registrations = LinkedBlockingQueue<Int>()
        val starts = AtomicInteger()
        val stops = AtomicInteger()
        val on = AtomicBoolean(true)
        val finished = CompletableFuture<Unit>()
        val scan = BeanScanWait<String>(name, {
            registrations.put(starts.incrementAndGet())
            throw IOException("registration failed")
        }, { stops.incrementAndGet() })
        val worker = Thread {
            try {
                scan.loop(on::get, { scan.await(); true }, { true }, {})
                finished.complete(Unit)
            } catch (e: Throwable) { finished.completeExceptionally(e) }
        }.apply { start() }
        try {
            assertEquals(1, registrations.poll(2, TimeUnit.SECONDS))
            assertNull(registrations.poll(1, TimeUnit.SECONDS))
            assertEquals(1, starts.get())
            assertEquals(1, stops.get())
            on.set(false)
            worker.interrupt()  // BeanLink.stop() sets on=false and interrupts the same loop
            finished.get(500, TimeUnit.MILLISECONDS)
            worker.join(500)
            assertFalse(worker.isAlive)
            assertEquals(1, starts.get())
        } finally { on.set(false); worker.interrupt(); worker.join(2000) }
    }

    @Test fun dropRegistersBackgroundScanAndDuplicateResultsConnectOnce() {
        val registered = LinkedBlockingQueue<Int>()
        val starts = AtomicInteger()
        val stops = AtomicInteger()
        val attempts = LinkedBlockingQueue<String>()
        val drop = CountDownLatch(1)
        val finished = CompletableFuture<Unit>()
        val scan = BeanScanWait<String>(name, { registered.put(starts.incrementAndGet()) }, { stops.incrementAndGet() })
        val worker = Thread {
            try {
                attempts.put(scan.await())
                drop.await()  // the session owns the only connection until it drops
                attempts.put(scan.await())
                finished.complete(Unit)
            } catch (e: Throwable) { finished.completeExceptionally(e) }
        }.apply { start() }
        try {
            assertEquals(1, registered.poll(2, TimeUnit.SECONDS))
            assertFalse(scan.deliver("D3200-FFFF", true, "another bean"))
            assertFalse(scan.deliver(name, false, "not connectable yet"))
            assertTrue(scan.deliver(name, true, "foreground"))
            assertFalse(scan.deliver(name, true, "duplicate"))
            assertEquals("foreground", attempts.poll(2, TimeUnit.SECONDS))
            assertFalse(scan.deliver(name, true, "late during session"))
            drop.countDown()
            assertEquals(2, registered.poll(2, TimeUnit.SECONDS))
            assertTrue(scan.deliver(name, true, "screen off, new address"))
            assertFalse(scan.deliver(name, true, "duplicate background delivery"))
            assertEquals("screen off, new address", attempts.poll(2, TimeUnit.SECONDS))
            finished.get(2, TimeUnit.SECONDS)
            assertTrue(attempts.isEmpty())
            assertEquals(2, stops.get())
        } finally { worker.interrupt(); worker.join(2000) }
    }

    @Test fun stopInterruptsWaitAndUnregistersScan() {
        val registered = CountDownLatch(1)
        val stopped = CountDownLatch(1)
        val interrupted = CompletableFuture<Boolean>()
        val scan = BeanScanWait<String>(name, { registered.countDown() }, { stopped.countDown() })
        val worker = Thread {
            try { scan.await(); interrupted.complete(false) }
            catch (_: InterruptedException) { interrupted.complete(true) }
        }.apply { start() }
        try {
            assertTrue(registered.await(2, TimeUnit.SECONDS))
            worker.interrupt()
            assertTrue(interrupted.get(2, TimeUnit.SECONDS))
            assertTrue(stopped.await(2, TimeUnit.SECONDS))
            assertFalse(scan.deliver(name, true, "late after stop"))
        } finally { worker.interrupt(); worker.join(2000) }
    }

    @Test fun scanFailureUnregistersAndAllowsAnotherRequest() {
        val stops = AtomicInteger()
        lateinit var scan: BeanScanWait<String>
        var fails = true
        scan = BeanScanWait(name, {
            if (fails) scan.fail(IOException("Bluetooth off"))
            else scan.deliver(name, true, "Bluetooth back on")
        }, { stops.incrementAndGet() })
        try { scan.await(); fail("scan should fail") }
        catch (e: IOException) { assertEquals("Bluetooth off", e.message) }
        assertEquals(1, stops.get())
        fails = false
        assertEquals("Bluetooth back on", scan.await())
        assertEquals(2, stops.get())
    }

    @Test fun registrationFailureStillUnregisters() {
        var stopped = false
        val scan = BeanScanWait<String>(name, { throw IOException("registration failed") }, { stopped = true })
        try { scan.await(); fail("registration should fail") }
        catch (e: IOException) { assertEquals("registration failed", e.message) }
        assertTrue(stopped)
        assertFalse(scan.deliver(name, true, "late"))
    }

    private class DiscoveryHarness {
        val now = AtomicLong(10_000)
        val starts = mutableListOf<Pair<BeanDiscovery.Mode, String>>()
        val stops = mutableListOf<String>()
        val reports = mutableListOf<Map<String, Any?>>()
        var available = true
        var foreground = true
        var onStart: (BeanDiscovery.Mode, String) -> Boolean = { _, id ->
            assertTrue(discovery.deliver(id, "D3200-ABCD", true, "found")); true
        }
        val discovery: BeanDiscovery<String> = BeanDiscovery("D3200-ABCD", now::get, { mode, id ->
            assertEquals("previous scanner must stop before next registration", starts.size, stops.size)
            starts += mode to id
            onStart(mode, id)
        }, { _, id -> stops += id }, { reports += it })
        fun rollover() = discovery.again(BeanSync.Again("rotation", naturalRollover = true))
        fun find() = discovery.find(foreground) { available }
        fun modes() = starts.map { it.first }
    }

    @Test fun naturalRolloverGetsOneFastWindowAndSuccessStopsBeforeReturning() {
        val h = DiscoveryHarness()
        h.rollover()
        assertEquals("found", h.find())
        assertEquals(listOf(BeanDiscovery.Mode.FAST), h.modes())
        assertEquals(h.starts.map { it.second }, h.stops)
        val phase = h.starts.single().second
        assertFalse(h.discovery.deliver(phase, name, true, "late"))
        h.find()  // A failed connection/retry cannot refresh the window.
        assertEquals(listOf(BeanDiscovery.Mode.FAST, BeanDiscovery.Mode.POWER), h.modes())
        val timing = h.reports.first { it["outcome"] == "found" }
        assertEquals("LOW_LATENCY", timing["requestedMode"])
        assertEquals(2000L, timing["budgetMs"])
        assertEquals("natural_rollover", timing["reason"])
    }

    @Test fun fastTimeoutIncludesRegistrationAndRejectsExpiredResultsAndErrorsInPowerPhase() {
        val h = DiscoveryHarness()
        h.rollover()
        var old = ""
        h.onStart = { mode, id ->
            if (mode == BeanDiscovery.Mode.FAST) {
                old = id
                assertFalse(h.discovery.deliver(id, name, false, "not connectable"))
                h.now.addAndGet(2000)  // Registration itself consumed the entire budget.
                assertFalse(h.discovery.deliver(id, name, true, "at deadline"))
            } else {
                assertNotEquals(old, id)
                h.discovery.fail(old, IOException("late fast error"))
                assertFalse(h.discovery.deliver(old, name, true, "late fast result"))
                assertFalse(h.discovery.deliver(id, "D3200-FFFF", true, "other bean"))
                assertTrue(h.discovery.deliver(id, name, true, "power result"))
                assertFalse(h.discovery.deliver(id, name, true, "duplicate"))
            }
            true
        }
        assertEquals("power result", h.find())
        assertEquals(listOf(BeanDiscovery.Mode.FAST, BeanDiscovery.Mode.POWER), h.modes())
        assertEquals(h.starts.map { it.second }, h.stops)
        assertEquals(2000L, h.reports.first { it["outcome"] == "timeout" }["elapsedMs"])
        assertEquals(600_000L, h.reports.last()["budgetMs"])
        assertFalse(h.discovery.wait.retrying)  // Normal expiry adds no backoff.
    }

    @Test fun fastRejectionExceptionAndSystemErrorFallBackOnlyOnce() {
        for (failure in 0..2) {
            val h = DiscoveryHarness()
            h.rollover()
            h.onStart = { mode, id ->
                if (mode == BeanDiscovery.Mode.POWER) {
                    assertTrue(h.discovery.deliver(id, name, true, "fallback")); true
                } else when (failure) {
                    0 -> false
                    1 -> throw IOException("start failure")
                    else -> { h.discovery.fail(id, IOException("system error")); true }
                }
            }
            assertEquals("fallback", h.find())
            h.find()
            assertEquals(listOf(BeanDiscovery.Mode.FAST, BeanDiscovery.Mode.POWER, BeanDiscovery.Mode.POWER), h.modes())
            assertEquals(h.starts.map { it.second }, h.stops)
            assertFalse(h.discovery.wait.retrying)
        }
    }

    @Test fun scanPhaseReportCountsCallbacksOfItsOwnRegistrationOnly() {
        val h = DiscoveryHarness()
        h.find()  // first round: found with no broadcast counted
        val old = h.starts.single().second
        h.onStart = { _, id ->
            h.discovery.heard(old, 3, true)  // the ended round's late broadcast counts nowhere
            h.discovery.heard(id, 2, false)  // other beans only
            h.discovery.heard(id, 0, false)  // an error broadcast
            h.discovery.heard(id, 1, true)
            assertTrue(h.discovery.deliver(id, name, true, "found")); true
        }
        h.find()
        val (first, second) = h.reports.filter { it["outcome"] == "found" }.map { listOf(it["callbacks"], it["results"], it["sawBean"]) }
        assertEquals(listOf(0, 0, false), first)
        assertEquals(listOf(3, 3, true), second)
    }

    @Test fun onlyNaturalRolloverWithForegroundImportanceAndCooldownQualifies() {
        val h = DiscoveryHarness()
        h.find()  // Initial addition/drop/error: no allowance.
        h.discovery.again(BeanSync.Again("cut or stall"))
        h.find()
        h.rollover(); h.foreground = false; h.find()
        h.foreground = true; h.find()  // Background rejection consumes the allowance too.
        assertTrue(h.modes().all { it == BeanDiscovery.Mode.POWER })
        h.rollover(); h.find()
        h.now.addAndGet(59_999); h.rollover(); h.find()
        assertEquals(1, h.modes().count { it == BeanDiscovery.Mode.FAST })
        h.now.incrementAndGet(); h.find()  // Cooldown expiry cannot resurrect consumed allowance.
        assertEquals(1, h.modes().count { it == BeanDiscovery.Mode.FAST })
        h.rollover(); h.find()
        assertEquals(2, h.modes().count { it == BeanDiscovery.Mode.FAST })
    }

    @Test fun unavailableBeforeFindConsumesRolloverWithoutRegistering() {
        val h = DiscoveryHarness()
        h.rollover(); h.available = false
        assertNull(h.find())
        assertTrue(h.starts.isEmpty())
        h.available = true; h.find()
        assertEquals(listOf(BeanDiscovery.Mode.POWER), h.modes())
    }

    @Test fun bluetoothOffOrCancellationDuringFastScanNeverRegistersFallback() {
        for (cancel in listOf(false, true)) {
            val h = DiscoveryHarness()
            h.rollover()
            h.onStart = { _, _ ->
                h.available = false
                if (cancel) throw InterruptedException()
                h.discovery.unavailable(BeanScanWait.Unavailable())
                true
            }
            if (cancel) assertThrows(InterruptedException::class.java) { h.find() }
            else assertThrows(BeanScanWait.Unavailable::class.java) { h.find() }
            assertEquals(listOf(BeanDiscovery.Mode.FAST), h.modes())
            assertEquals(h.starts.map { it.second }, h.stops)
            h.available = true
            h.onStart = { _, id -> h.discovery.deliver(id, name, true, "recovered"); true }
            h.find()
            assertEquals(BeanDiscovery.Mode.POWER, h.modes().last())
        }
    }

    @Test fun cancellationAtFastTimeoutDoesNotStartPowerScan() {
        val h = DiscoveryHarness()
        h.rollover()
        h.onStart = { _, _ -> h.now.addAndGet(2000); h.available = false; true }
        assertNull(h.find())
        assertEquals(listOf(BeanDiscovery.Mode.FAST), h.modes())
        assertEquals(h.starts.map { it.second }, h.stops)
    }

    @Test fun oldPowerDeliveryCannotEnterNewPowerRegistration() {
        val h = DiscoveryHarness()
        h.find()
        val old = h.starts.single().second
        h.onStart = { _, id ->
            h.discovery.fail(old, IOException("expired power failure"))
            assertFalse(h.discovery.deliver(old, name, true, "expired power result"))
            assertTrue(h.discovery.deliver(id, name, true, "current"))
            true
        }
        assertEquals("current", h.find())
    }

    @Test fun actualTwoSecondWaitIsCancelledPromptlyAndStopsScanner() {
        val started = CountDownLatch(1)
        val stopped = CountDownLatch(1)
        val result = CompletableFuture<Boolean>()
        val discovery = BeanDiscovery<String>(name, { System.nanoTime() / 1_000_000 },
            { _, _ -> started.countDown(); true }, { _, _ -> stopped.countDown() })
        discovery.again(BeanSync.Again("rotation", true))
        val worker = Thread {
            try { discovery.find(true) { true }; result.complete(false) }
            catch (_: InterruptedException) { result.complete(true) }
        }.apply { start() }
        try {
            assertTrue(started.await(2, TimeUnit.SECONDS))
            worker.interrupt()
            assertTrue(result.get(500, TimeUnit.MILLISECONDS))
            assertTrue(stopped.await(500, TimeUnit.MILLISECONDS))
        } finally { worker.interrupt(); worker.join(2000) }
    }


    @Test fun realFastDeadlineStopsBeforePowerRegistration() {
        val began = System.nanoTime()
        val modes = mutableListOf<BeanDiscovery.Mode>()
        var stopped = false
        lateinit var discovery: BeanDiscovery<String>
        discovery = BeanDiscovery(name, { System.nanoTime() / 1_000_000 }, { mode, id ->
            modes += mode
            if (mode == BeanDiscovery.Mode.POWER) {
                assertTrue(stopped)
                assertTrue(System.nanoTime() - began >= TimeUnit.MILLISECONDS.toNanos(1900))
                discovery.deliver(id, name, true, "fallback")
            }
            true
        }, { _, _ -> stopped = true })
        discovery.again(BeanSync.Again("rotation", true))
        assertEquals("fallback", discovery.find(true) { true })
        assertEquals(listOf(BeanDiscovery.Mode.FAST, BeanDiscovery.Mode.POWER), modes)
    }

    @Test fun cleanupFailureDoesNotStartFallbackScanner() {
        val modes = mutableListOf<BeanDiscovery.Mode>()
        lateinit var discovery: BeanDiscovery<String>
        discovery = BeanDiscovery(name, { 10_000L }, { mode, id ->
            modes += mode
            discovery.fail(id, IOException("registration failed"))
            true
        }, { _, _ -> throw IOException("stop failed") })
        discovery.again(BeanSync.Again("rotation", true))
        val e = assertThrows(IOException::class.java) { discovery.find(true) { true } }
        assertEquals("stop failed", e.message)
        assertEquals(listOf(BeanDiscovery.Mode.FAST), modes)
    }


    @Test fun cancellationAfterMatchingAdvertisementCannotStartConnectionOrFallback() {
        for (natural in listOf(false, true)) {
            val h = DiscoveryHarness()
            if (natural) h.rollover()
            h.onStart = { _, id ->
                assertTrue(h.discovery.deliver(id, name, true, "found before cancellation"))
                h.available = false
                true
            }
            assertNull(h.find())
            assertEquals(1, h.starts.size)
            assertEquals(h.starts.map { it.second }, h.stops)
        }
    }

    @Test fun bluetoothUnavailableDuringRegistrationIsNotTreatedAsScanError() {
        val h = DiscoveryHarness()
        h.rollover()
        h.onStart = { _, _ -> throw BeanScanWait.Unavailable() }
        assertThrows(BeanScanWait.Unavailable::class.java) { h.find() }
        assertEquals(listOf(BeanDiscovery.Mode.FAST), h.modes())
        assertEquals(h.starts.map { it.second }, h.stops)
    }


    @Test fun cleanupFailurePreservesBluetoothOffAndCancellationWithoutFallback() {
        for (cancel in listOf(false, true)) {
            val modes = mutableListOf<BeanDiscovery.Mode>()
            lateinit var discovery: BeanDiscovery<String>
            discovery = BeanDiscovery(name, { 10_000L }, { mode, _ ->
                modes += mode
                if (cancel) throw InterruptedException("cancelled")
                discovery.unavailable(BeanScanWait.Unavailable())
                true
            }, { _, _ -> throw IOException("cleanup failure") })
            discovery.again(BeanSync.Again("rotation", true))
            val error = if (cancel) assertThrows(InterruptedException::class.java) { discovery.find(true) { true } }
                else assertThrows(BeanScanWait.Unavailable::class.java) { discovery.find(true) { true } }
            assertEquals("cleanup failure", error.suppressed.single().message)
            assertEquals(listOf(BeanDiscovery.Mode.FAST), modes)
        }
    }

    @Test fun interruptedWaitWithFailingCleanupExitsOriginalLoopWithoutRetry() {
        val started = CountDownLatch(1)
        val finished = CompletableFuture<Unit>()
        val retries = AtomicInteger()
        val discovery = BeanDiscovery<String>(name, { System.nanoTime() / 1_000_000 },
            { _, _ -> started.countDown(); true }, { _, _ -> throw IOException("cleanup failure") })
        discovery.again(BeanSync.Again("rotation", true))
        val worker = Thread {
            try {
                discovery.wait.loop({ true }, { discovery.find(true) { true }; true },
                    { retries.incrementAndGet(); true }, {})
                finished.complete(Unit)
            } catch (e: Throwable) { finished.completeExceptionally(e) }
        }.apply { start() }
        try {
            assertTrue(started.await(2, TimeUnit.SECONDS))
            worker.interrupt()
            finished.get(500, TimeUnit.MILLISECONDS)
            assertEquals(0, retries.get())
        } finally { worker.interrupt(); worker.join(2000) }
    }


    @Test fun cleanupFailureBacksOffInsteadOfSpinningRegistrations() {
        val registrations = LinkedBlockingQueue<String>()
        val finished = CompletableFuture<Unit>()
        lateinit var discovery: BeanDiscovery<String>
        discovery = BeanDiscovery(name, { System.nanoTime() / 1_000_000 }, { _, id ->
            registrations.put(id)
            discovery.fail(id, IOException("registration failed"))
            true
        }, { _, _ -> throw IOException("cleanup failure") })
        discovery.again(BeanSync.Again("rotation", true))
        val worker = Thread {
            try {
                discovery.wait.loop({ true }, { discovery.find(true) { true }; true }, { true }, {})
                finished.complete(Unit)
            } catch (e: Throwable) { finished.completeExceptionally(e) }
        }.apply { start() }
        try {
            assertNotNull(registrations.poll(2, TimeUnit.SECONDS))
            assertNull(registrations.poll(1, TimeUnit.SECONDS))
            worker.interrupt()
            finished.get(500, TimeUnit.MILLISECONDS)
        } finally { worker.interrupt(); worker.join(2000) }
    }


    @Test fun cancellingFastScanRetriesStopImmediatelyBeforeOwnerExits() {
        val started = CountDownLatch(1)
        val finished = CompletableFuture<Unit>()
        val stops = AtomicInteger()
        val cancelledPi = AtomicBoolean()
        val registered = AtomicBoolean()
        val retries = AtomicInteger()
        val discovery = BeanDiscovery<String>(name, { System.nanoTime() / 1_000_000 }, { _, _ ->
            registered.set(true); started.countDown(); true
        }, { _, _ ->
            beanStopScan({
                if (stops.incrementAndGet() == 1) throw IOException("transient stop failure")
                registered.set(false)
            }, { cancelledPi.set(true) })
        })
        discovery.again(BeanSync.Again("rotation", true))
        val worker = Thread {
            try {
                discovery.wait.loop({ true }, { discovery.find(true) { true }; true },
                    { retries.incrementAndGet(); true }, {})
                finished.complete(Unit)
            } catch (e: Throwable) { finished.completeExceptionally(e) }
        }.apply { start() }
        try {
            assertTrue(started.await(2, TimeUnit.SECONDS))
            worker.interrupt()
            finished.get(500, TimeUnit.MILLISECONDS)
            assertEquals(2, stops.get())
            assertFalse(registered.get())
            assertTrue(cancelledPi.get())
            assertEquals(0, retries.get())
        } finally { worker.interrupt(); worker.join(2000) }
    }

    @Test fun permanentStopFailureStillCancelsOriginalIntentAndCannotFallback() {
        for (cancel in listOf(false, true)) {
            val order = mutableListOf<String>()
            var registrations = 0
            val original = IOException("stop failed")
            val discovery = BeanDiscovery<String>(name, { 10_000L }, { _, _ ->
                registrations++
                if (cancel) throw InterruptedException("cancelled")
                false
            }, { _, _ ->
                beanStopScan({ order += "stop"; throw original }, { order += "cancel" },
                    { throw IOException("diagnostic failed") })
            })
            discovery.again(BeanSync.Again("rotation", true))
            if (cancel) {
                val e = assertThrows(InterruptedException::class.java) { discovery.find(true) { true } }
                assertTrue(e.suppressed.single() is BeanScanWait.CleanupFailure)
            } else assertThrows(BeanScanWait.CleanupFailure::class.java) { discovery.find(true) { true } }
            assertEquals(listOf("stop", "stop", "cancel"), order)
            assertEquals(1, registrations)
        }
    }

    @Test fun fastTimeoutRecoversTransientStopFailureBeforeStartingPowerScan() {
        val h = DiscoveryHarness()
        val order = mutableListOf<String>()
        var stops = 0
        lateinit var discovery: BeanDiscovery<String>
        discovery = BeanDiscovery(name, h.now::get, { mode, id ->
            order += mode.name
            if (mode == BeanDiscovery.Mode.FAST) h.now.addAndGet(2000)
            else {
                assertEquals(listOf("FAST", "stop", "stop", "cancel", "POWER"), order)
                discovery.deliver(id, name, true, "power")
            }
            true
        }, { _, _ ->
            beanStopScan({ order += "stop"; if (++stops == 1) throw IOException("transient") }, { order += "cancel" })
        })
        discovery.again(BeanSync.Again("rotation", true))
        assertEquals("power", discovery.find(true) { true })
        assertEquals(3, stops)
    }

    @Test fun normalStopPrecedesAuxiliaryCancellation() {
        val order = mutableListOf<String>()
        beanStopScan({ order += "stop" }, { order += "cancel" })
        assertEquals(listOf("stop", "cancel"), order)
    }

    @Test fun timingObserverFailuresCannotChangeScanSuccessOrFallback() {
        for (rejectFast in listOf(false, true)) {
            val modes = mutableListOf<BeanDiscovery.Mode>()
            lateinit var discovery: BeanDiscovery<String>
            discovery = BeanDiscovery(name, { 10_000L }, { mode, id ->
                modes += mode
                if (mode == BeanDiscovery.Mode.FAST && rejectFast) false
                else { discovery.deliver(id, name, true, "found"); true }
            }, { _, _ -> }, { throw IOException("timing store unavailable") })
            discovery.again(BeanSync.Again("rotation", true))
            assertEquals("found", discovery.find(true) { true })
            assertEquals(if (rejectFast) listOf(BeanDiscovery.Mode.FAST, BeanDiscovery.Mode.POWER)
                else listOf(BeanDiscovery.Mode.FAST), modes)
        }
    }

    @Test fun timingObserverCannotMaskCancellationBluetoothOffOrCleanupFailure() {
        for (kind in 0..2) {
            val modes = mutableListOf<BeanDiscovery.Mode>()
            val discovery = BeanDiscovery<String>(name, { 10_000L }, { mode, _ ->
                modes += mode
                when (kind) {
                    0 -> throw InterruptedException("original cancellation")
                    1 -> throw BeanScanWait.Unavailable()
                    else -> false
                }
            }, { _, _ -> if (kind == 2) throw IOException("original cleanup failure") },
                { throw IOException("timing failed") })
            discovery.again(BeanSync.Again("rotation", true))
            when (kind) {
                0 -> assertEquals("original cancellation", assertThrows(InterruptedException::class.java) { discovery.find(true) { true } }.message)
                1 -> assertThrows(BeanScanWait.Unavailable::class.java) { discovery.find(true) { true } }
                else -> assertEquals("original cleanup failure", assertThrows(BeanScanWait.CleanupFailure::class.java) { discovery.find(true) { true } }.message)
            }
            assertEquals(listOf(BeanDiscovery.Mode.FAST), modes)
        }
    }


    @Test fun restoredFastPowerAndLegacyKeepOriginalIdentityAcrossCleanupFailure() = restoredIdentity(false)
    @Test fun cancellingRestoredFastPowerAndLegacyKeepsOriginalIdentityForNextFind() = restoredIdentity(true)

    private fun restoredIdentity(cancel: Boolean) {
        for (identity in listOf("restored-fast", "restored-power", "legacy")) {
            // Like PendingIntent: cancel retires a token; reconstructing the same action/data gives a NEW object.
            data class Token(val identity: String)
            val original = Token(identity)
            var factoryToken = original
            val stopped = mutableListOf<Token>()
            var failOldStop = true
            var oldStopReturned = false
            val owner = BeanScanOwner<Token>({ token ->
                stopped += token
                if (token === original) {
                    if (failOldStop) throw IOException("old scan stop failed")
                    oldStopReturned = true
                }
            }, { token -> if (token === factoryToken) factoryToken = Token(identity) })
            var restored = false
            var cancelDuringRestore = cancel
            var starts = 0
            lateinit var discovery: BeanDiscovery<String>
            discovery = BeanDiscovery(name, { 10_000L }, { _, id ->
                owner.clean()  // Same production rule: finish the owned identity before reconstructing anything.
                if (!restored) {
                    owner.own(factoryToken)
                    if (cancelDuringRestore) throw InterruptedException("cancel during restore")
                    owner.clean()
                    restored = true
                }
                assertTrue("must explicitly stop the ORIGINAL before a new registration", oldStopReturned)
                owner.own(Token("new registration"))
                starts++
                discovery.deliver(id, name, true, "found")
                true
            }, { _, _ -> owner.clean() })
            discovery.again(BeanSync.Again("rotation", true))
            if (cancel) {
                val e = assertThrows(InterruptedException::class.java) { discovery.find(true) { true } }
                assertTrue(e.suppressed.single() is BeanScanWait.CleanupFailure)
            } else assertThrows(BeanScanWait.CleanupFailure::class.java) { discovery.find(true) { true } }
            assertEquals(0, starts)
            assertTrue(stopped.all { it === original })
            assertNotSame(original, factoryToken)
            assertThrows(IllegalStateException::class.java) { owner.own(factoryToken) }
            val stopsBeforeRetry = stopped.size
            failOldStop = false; cancelDuringRestore = false
            assertEquals("found", discovery.find(true) { true })
            assertSame(original, stopped[stopsBeforeRetry])
            assertEquals(1, starts)
            assertTrue(oldStopReturned)
        }
    }

}
