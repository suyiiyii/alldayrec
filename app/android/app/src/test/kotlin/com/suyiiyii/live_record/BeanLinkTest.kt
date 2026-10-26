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
}
