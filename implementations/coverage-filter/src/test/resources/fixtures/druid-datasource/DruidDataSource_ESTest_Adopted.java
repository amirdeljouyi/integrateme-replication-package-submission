package com.alibaba.druid.pool;
import java.sql.SQLException;
import java.sql.SQLWarning;
import java.util.Date;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import org.junit.Test;
import static org.evosuite.runtime.EvoAssertions.verifyException;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNotSame;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;
public class DruidDataSource_ESTest_Adopted {
    @Test(timeout = 4000)
    public void testResolveDriver_withoutUrl_throwsSQLException() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(false);
        try {
            dataSource.resolveDriver();
            fail("Expecting exception: SQLException");
        } catch (SQLException e) {
            // 
            // url not set
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testHandleConnectionException_withNullConnection_throwsNullPointerException() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        SQLWarning sqlWarning = new SQLWarning("tSVE<.", "tSVE<.", 1433);
        // Undeclared exception!
        try {
            dataSource.handleConnectionException(((DruidPooledConnection) (null)), ((Throwable) (sqlWarning)), "tSVE<.");
            fail("Expecting exception: NullPointerException");
        } catch (NullPointerException e) {
            // 
            // Cannot invoke \"com.alibaba.druid.pool.DruidPooledConnection.getConnectionHolder()\" because \"pooledConnection\" is null
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testGetRawDriverMajorVersion_defaultIsMinusOne() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        int majorVersion = dataSource.getRawDriverMajorVersion();
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isEnable());
        assertEquals(-1, majorVersion);
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testGetProperties_returnsEmptyJsonByDefault() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(false);
        String propertiesJson = dataSource.getProperties();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isResetStatEnable());
        assertEquals("{}", propertiesJson);
    }

    @Test(timeout = 4000)
    public void testGetDataSourceStat_default() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.getDataSourceStat();
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
    }

    @Test(timeout = 4000)
    public void testGetCreateCount_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        long createCount = dataSource.getCreateCount();
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isResetStatEnable());
        assertEquals(0L, createCount);
    }

    @Test(timeout = 4000)
    public void testGetConnectCount_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        long connectCount = dataSource.getConnectCount();
        assertEquals(0L, connectCount);
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
    }

    @Test(timeout = 4000)
    public void testGetCloseCount_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        long closeCount = dataSource.getCloseCount();
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isEnable());
        assertEquals(0L, closeCount);
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testGetActiveCount_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        int activeCount = dataSource.getActiveCount();
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isEnable());
        assertEquals(0, activeCount);
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testCloneDruidDataSource_copiesTestOnBorrow() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.setTestOnBorrow(true);
        DruidDataSource cloned = dataSource.cloneDruidDataSource();
        assertNotSame(cloned, dataSource);
        assertTrue(cloned.isResetStatEnable());
        assertTrue(cloned.isLogDifferentThread());
        assertTrue(cloned.isEnable());
        assertTrue(cloned.isTestOnBorrow());
    }

    @Test(timeout = 4000)
    public void testCloneDruidDataSource_preservesDupCloseLogEnable() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.setDupCloseLogEnable(true);
        DruidDataSource cloned = dataSource.cloneDruidDataSource();
        assertTrue(cloned.isLogDifferentThread());
        assertTrue(cloned.isResetStatEnable());
        assertTrue(cloned.isDupCloseLogEnable());
        assertNotSame(cloned, dataSource);
        assertTrue(cloned.isEnable());
    }

    @Test(timeout = 4000)
    public void testGetWallStatValue_defaultInvocation() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.getWallStatValue(true);
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testGetActivePeakTime_initiallyNull() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        Date activePeakTime = dataSource.getActivePeakTime();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isLogDifferentThread());
        assertNull(activePeakTime);
    }

    @Test(timeout = 4000)
    public void testShrink_withFlags_noException() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.shrink(true, true);
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testGetFilterClassNames_defaultNotNull() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(false);
        List<String> filterClassNames = dataSource.getFilterClassNames();
        assertTrue(dataSource.isEnable());
        assertNotNull(filterClassNames);
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testGetPoolingPeakTime_initiallyNull() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        Date poolingPeakTime = dataSource.getPoolingPeakTime();
        assertNull(poolingPeakTime);
    }

    @Test(timeout = 4000)
    public void testClose_doesNotDisableDataSource() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.close();
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testGetConnection_withTimeoutZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.getConnection(((long) (0)));
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
    }

    @Test(timeout = 4000)
    public void testInit_withoutUrl_throwsSQLException() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(false);
        try {
            dataSource.init();
            fail("Expecting exception: SQLException");
        } catch (SQLException e) {
            // 
            // url not set
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testResetStat_incrementsResetCount() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        dataSource.resetStat();
        assertEquals(1L, dataSource.getResetCount());
    }

    @Test(timeout = 4000)
    public void testGetErrorCount_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(false);
        long errorCount = dataSource.getErrorCount();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isLogDifferentThread());
        assertEquals(0L, errorCount);
        assertTrue(dataSource.isResetStatEnable());
    }

    @Test(timeout = 4000)
    public void testGetPoolingCount_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        int poolingCount = dataSource.getPoolingCount();
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isEnable());
        assertEquals(0, poolingCount);
    }

    @Test(timeout = 4000)
    public void testGetConnection_defaultInvocation() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.getConnection();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
    }

    @Test(timeout = 4000)
    public void testIsResetStatEnable_defaultTrue() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        boolean resetStatEnabled = dataSource.isResetStatEnable();
        assertTrue(dataSource.isEnable());
        assertTrue(resetStatEnabled);
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testGetConnectErrorCount_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        long connectErrorCount = dataSource.getConnectErrorCount();
        assertEquals(0L, connectErrorCount);
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testPreDeregister_noExceptions() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.preDeregister();
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testTryGetConnection_defaultInvocation() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        dataSource.tryGetConnection();
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testUnwrap_withScheduledThreadPoolExecutorClass() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        Class<ScheduledThreadPoolExecutor> targetType = ScheduledThreadPoolExecutor.class;
        dataSource.unwrap(targetType);
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testIsWrapperFor_returnsFalseForUnrelatedType() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        Class<ScheduledThreadPoolExecutor> targetType = ScheduledThreadPoolExecutor.class;
        boolean isWrapper = dataSource.isWrapperFor(targetType);
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
        assertFalse(isWrapper);
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testGetWallStatMap_defaultInvocation() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(false);
        dataSource.getWallStatMap();
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testGetPoolingConnectionInfo_defaultInvocation() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.getPoolingConnectionInfo();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
    }

    @Test(timeout = 4000)
    public void testRemoveAbandoned_returnsZeroByDefault() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(false);
        int removedCount = dataSource.removeAbandoned();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
        assertEquals(0, removedCount);
    }

    @Test(timeout = 4000)
    public void testDestroyTask_run_noExceptions() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        DruidDataSource.DestroyTask destroyTask = dataSource.new DestroyTask();
        destroyTask.run();
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testDestroyConnectionThread_run_throwsNPE() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        DruidDataSource.DestroyConnectionThread destroyConnectionThread = dataSource.new DestroyConnectionThread("ZdIS^gE2H");
        // Undeclared exception!
        try {
            destroyConnectionThread.run();
            fail("Expecting exception: NullPointerException");
        } catch (NullPointerException e) {
            // 
            // Cannot invoke \"com.alibaba.druid.pool.DruidDataSource$DestroyTask.run()\" because the return value of \"com.alibaba.druid.pool.DruidDataSource.access$1300(com.alibaba.druid.pool.DruidDataSource)\" is null
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource$DestroyConnectionThread", e);
        }
    }

    @Test(timeout = 4000)
    public void testGetStatValueAndReset_withoutStat_throwsNPE() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        // Undeclared exception!
        try {
            dataSource.getStatValueAndReset();
            fail("Expecting exception: NullPointerException");
        } catch (NullPointerException e) {
            // 
            // Cannot invoke \"com.alibaba.druid.stat.JdbcDataSourceStat.getConnectionHoldHistogram()\" because the return value of \"com.alibaba.druid.pool.DruidDataSource.getDataSourceStat()\" is null
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testInitCheck_defaultInvocation() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        dataSource.initCheck();
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testFill_withCapacity_throwsSQLException() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        try {
            dataSource.fill(72);
            fail("Expecting exception: SQLException");
        } catch (SQLException e) {
            // 
            // url not set
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testSetConnectProperties_roundTrip() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        Properties connectProperties = dataSource.getConnectProperties();
        dataSource.setConnectProperties(connectProperties);
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
    }

    @Test(timeout = 4000)
    public void testSetEnable_true_updatesFlags() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.setEnable(true);
        assertTrue(dataSource.isResetStatEnable());
        assertEquals(0L, dataSource.getNotEmptySignalCount());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testCreateConnectionThread_constructor_noExceptions() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        DruidDataSource.CreateConnectionThread createConnectionThread = dataSource.new CreateConnectionThread("PoolingPeak");
    }

    @Test(timeout = 4000)
    public void testCreateConnectionTask_run_noExceptions() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(false);
        DruidDataSource.CreateConnectionTask createConnectionTask = dataSource.new CreateConnectionTask();
        createConnectionTask.run();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isResetStatEnable());
    }

    @Test(timeout = 4000)
    public void testCreateConnectionTask_constructorWithCreateError() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        DruidDataSource.CreateConnectionTask createConnectionTask = dataSource.new CreateConnectionTask(true);
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
    }

    @Test(timeout = 4000)
    public void testSetCheckExecuteTime_toFalse() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.setCheckExecuteTime(false);
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isLogDifferentThread());
        assertFalse(dataSource.isCheckExecuteTime());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testGetSqlStatMap_withoutStat_throwsNPE() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        // Undeclared exception!
        try {
            dataSource.getSqlStatMap();
            fail("Expecting exception: NullPointerException");
        } catch (NullPointerException e) {
            // 
            // Cannot invoke \"com.alibaba.druid.stat.JdbcDataSourceStat.getSqlStatMap()\" because the return value of \"com.alibaba.druid.pool.DruidDataSource.getDataSourceStat()\" is null
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testGetPooledConnection_withCredentials_throwsUnsupported() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        // Undeclared exception!
        try {
            dataSource.getPooledConnection("", "keepAliveErr");
            fail("Expecting exception: UnsupportedOperationException");
        } catch (UnsupportedOperationException e) {
            // 
            // Not supported by DruidDataSource
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testGetNotEmptySignalCount_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        long notEmptySignalCount = dataSource.getNotEmptySignalCount();
        assertEquals(0L, notEmptySignalCount);
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testIsUseGlobalDataSourceStat_defaultFalse() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        boolean useGlobalStat = dataSource.isUseGlobalDataSourceStat();
        assertTrue(dataSource.isEnable());
        assertFalse(useGlobalStat);
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testGetSqlStat_byIndex_withoutStat_throwsNPE() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        // Undeclared exception!
        try {
            dataSource.getSqlStat(0);
            fail("Expecting exception: NullPointerException");
        } catch (NullPointerException e) {
            // 
            // Cannot invoke \"com.alibaba.druid.stat.JdbcDataSourceStat.getSqlStat(int)\" because the return value of \"com.alibaba.druid.pool.DruidDataSource.getDataSourceStat()\" is null
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testGetPoolingPeak_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        int poolingPeak = dataSource.getPoolingPeak();
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isResetStatEnable());
        assertEquals(0, poolingPeak);
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testIsLoadSpifilterSkip_defaultFalse() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        boolean skip = dataSource.isLoadSpifilterSkip();
        assertTrue(dataSource.isResetStatEnable());
        assertFalse(skip);
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testGetConnection_withCredentials_setsPassword() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        dataSource.getConnection("zL|z", "QueryTimeout");
        assertEquals("QueryTimeout", dataSource.getPassword());
    }

    @Test(timeout = 4000)
    public void testFill_withoutCapacity_throwsSQLException() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.setLoadSpifilterSkip(true);
        try {
            dataSource.fill();
            fail("Expecting exception: SQLException");
        } catch (SQLException e) {
            // 
            // url not set
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testSetLogDifferentThread_toggleFalseThenAssert() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        assertTrue(dataSource.isLogDifferentThread());
        dataSource.setLogDifferentThread(false);
        assertFalse(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testShrink_withoutArgs_noExceptions() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.shrink();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isResetStatEnable());
    }

    @Test(timeout = 4000)
    public void testGetRecycleErrorCount_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        long recycleErrorCount = dataSource.getRecycleErrorCount();
        assertTrue(dataSource.isResetStatEnable());
        assertEquals(0L, recycleErrorCount);
    }

    @Test(timeout = 4000)
    public void testRegisterMbean_marksRegistered() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.registerMbean();
        assertTrue(dataSource.isMbeanRegistered());
    }

    @Test(timeout = 4000)
    public void testSetKeepAlive_true() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.setKeepAlive(true);
        assertTrue(dataSource.isKeepAlive());
    }

    @Test(timeout = 4000)
    public void testGetLockQueueLength_defaultInvocation() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.getLockQueueLength();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testPostDeregister_noExceptions() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.postDeregister();
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testGetRemoveAbandonedCount_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        long removeAbandonedCount = dataSource.getRemoveAbandonedCount();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
        assertEquals(0L, removeAbandonedCount);
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testGetVersion_defaultInvocation() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        dataSource.getVersion();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isResetStatEnable());
    }

    @Test(timeout = 4000)
    public void testGetReference_defaultInvocation() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.getReference();
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testGetInitStackTrace_defaultInvocation() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.getInitStackTrace();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testDump_defaultFormat() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        String dump = dataSource.dump();
        assertEquals("{\n\tCreateTime:\"2014-02-14 20:21:21\",\n\tActiveCount:0,\n\tPoolingCount:0,\n\tCreateCount:0,\n\tDestroyCount:0,\n\tCloseCount:0,\n\tConnectCount:0,\n\tConnections:[\n\t]\n}", dump);
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testGetSqlStat_byId_withoutStat_throwsNPE() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        // Undeclared exception!
        try {
            dataSource.getSqlStat(0L);
            fail("Expecting exception: NullPointerException");
        } catch (NullPointerException e) {
            // 
            // Cannot invoke \"com.alibaba.druid.stat.JdbcDataSourceStat.getSqlStat(long)\" because the return value of \"com.alibaba.druid.pool.DruidDataSource.getDataSourceStat()\" is null
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testClone_returnsDistinctInstance() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        DruidDataSource cloned = ((DruidDataSource) (dataSource.clone()));
        assertTrue(cloned.isEnable());
        assertTrue(cloned.isLogDifferentThread());
        assertTrue(cloned.isResetStatEnable());
        assertNotSame(cloned, dataSource);
    }

    @Test(timeout = 4000)
    public void testGetNotEmptyWaitThreadPeak_startsAtZero() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        int notEmptyWaitThreadPeak = dataSource.getNotEmptyWaitThreadPeak();
        assertTrue(dataSource.isEnable());
        assertTrue(dataSource.isResetStatEnable());
        assertEquals(0, notEmptyWaitThreadPeak);
        assertTrue(dataSource.isLogDifferentThread());
    }

    @Test(timeout = 4000)
    public void testSetPoolPreparedStatements_true() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.setPoolPreparedStatements(true);
        assertTrue(dataSource.isPoolPreparedStatements());
    }

    @Test(timeout = 4000)
    public void testSetAsyncInit_true() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.setAsyncInit(true);
        assertTrue(dataSource.isAsyncInit());
    }

    @Test(timeout = 4000)
    public void testGetStatData_withoutStat_throwsNPE() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(false);
        // Undeclared exception!
        try {
            dataSource.getStatData();
            fail("Expecting exception: NullPointerException");
        } catch (NullPointerException e) {
            // 
            // Cannot invoke \"com.alibaba.druid.stat.JdbcDataSourceStat.getConnectionHoldHistogram()\" because the return value of \"com.alibaba.druid.pool.DruidDataSource.getDataSourceStat()\" is null
            // 
            verifyException("com.alibaba.druid.pool.DruidDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testRestart_incrementsResetCount() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(false);
        dataSource.restart();
        assertEquals(1L, dataSource.getResetCount());
    }

    @Test(timeout = 4000)
    public void testGetCompositeData_withoutStat_throwsNPE() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        // Undeclared exception!
        try {
            dataSource.getCompositeData();
            fail("Expecting exception: NullPointerException");
        } catch (NullPointerException e) {
            // 
            // Cannot invoke \"com.alibaba.druid.stat.JdbcDataSourceStat.getConnectionStat()\" because \"stat\" is null
            // 
            verifyException("com.alibaba.druid.pool.DruidAbstractDataSource", e);
        }
    }

    @Test(timeout = 4000)
    public void testCreateAndStartDestroyThread_noExceptions() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource();
        dataSource.createAndStartDestroyThread();
        assertTrue(dataSource.isLogDifferentThread());
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isEnable());
    }

    @Test(timeout = 4000)
    public void testSanitizedUrl_masksPasswordsAndSensitiveParams() throws Throwable {
        String url = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        String expectedUrl = url;
        String urlNew = DruidDataSource.sanitizedUrl(url);
        assertEquals(expectedUrl, urlNew);

        url = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password";
        expectedUrl = url;
        urlNew = DruidDataSource.sanitizedUrl(url);
        assertEquals(expectedUrl, urlNew);

        url = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password=";
        expectedUrl = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password=<masked>";
        urlNew = DruidDataSource.sanitizedUrl(url);
        assertEquals(expectedUrl, urlNew);

        url = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password=12345678";
        expectedUrl = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password=<masked>";
        urlNew = DruidDataSource.sanitizedUrl(url);
        assertEquals(expectedUrl, urlNew);

        url = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password=12345678&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        expectedUrl = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password=<masked>&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        urlNew = DruidDataSource.sanitizedUrl(url);
        assertEquals(expectedUrl, urlNew);

        url = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password1=12345678&password=12345678&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        expectedUrl = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password1=<masked>&password=<masked>&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        urlNew = DruidDataSource.sanitizedUrl(url);
        assertEquals(expectedUrl, urlNew);

        url = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password2=12345678&password1=12345678&password=12345678&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        expectedUrl = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password2=<masked>&password1=<masked>&password=<masked>&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        urlNew = DruidDataSource.sanitizedUrl(url);
        assertEquals(expectedUrl, urlNew);

        url = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password3=12345678&password2=12345678&password1=12345678&password=12345678&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        expectedUrl = "jdbc:mysql://127.0.0.1:3306/druid?useUnicode=true&user=root&password3=<masked>&password2=<masked>&password1=<masked>&password=<masked>&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        urlNew = DruidDataSource.sanitizedUrl(url);
        assertEquals(expectedUrl, urlNew);

        url = "jdbc:mysql://127.0.0.1:3306/druid?clientCertificateKeyStorePassword=12345678&useUnicode=true&user=root&password3=12345678&password2=12345678&password1=12345678&password=12345678&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        expectedUrl = "jdbc:mysql://127.0.0.1:3306/druid?clientCertificateKeyStorePassword=<masked>&useUnicode=true&user=root&password3=<masked>&password2=<masked>&password1=<masked>&password=<masked>&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        urlNew = DruidDataSource.sanitizedUrl(url);
        assertEquals(expectedUrl, urlNew);

        url = "jdbc:mysql://127.0.0.1:3306/druid?trustCertificateKeyStorePassword=12345678&useUnicode=true&user=root&password3=12345678&password2=12345678&password1=12345678&password=12345678&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        expectedUrl = "jdbc:mysql://127.0.0.1:3306/druid?trustCertificateKeyStorePassword=<masked>&useUnicode=true&user=root&password3=<masked>&password2=<masked>&password1=<masked>&password=<masked>&characterEncoding=UTF-8&zeroDateTimeBehavior=convertToNull&useSSL=false&allowMultiQueries=true";
        urlNew = DruidDataSource.sanitizedUrl(url);
        assertEquals(expectedUrl, urlNew);
    }
}