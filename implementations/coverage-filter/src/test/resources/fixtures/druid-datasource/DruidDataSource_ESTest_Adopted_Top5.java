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
public class DruidDataSource_ESTest_Adopted_Top5 {
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
    public void testInitCheck_defaultInvocation() throws Throwable {
        DruidDataSource dataSource = new DruidDataSource(true);
        dataSource.initCheck();
        assertTrue(dataSource.isResetStatEnable());
        assertTrue(dataSource.isEnable());
    }
}
