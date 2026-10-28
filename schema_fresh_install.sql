-- ONLY for a fresh install. Do not run over existing tables without inspecting first.
CREATE DATABASE IF NOT EXISTS dripferti CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE dripferti;

CREATE TABLE IF NOT EXISTS sensor_readings (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  moisture_percent DECIMAL(5,2) NOT NULL,
  sensor_status VARCHAR(20) DEFAULT 'normal',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS flow_readings (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  flow_lpm DECIMAL(8,3) NOT NULL DEFAULT 0,
  total_liters DECIMAL(10,3) NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS irrigation_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  action ENUM('ON','OFF') NOT NULL,
  source ENUM('manual','automatic') DEFAULT 'manual',
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  ended_at TIMESTAMP NULL,
  notes VARCHAR(255) NULL
);

CREATE TABLE IF NOT EXISTS system_status (
  id INT AUTO_INCREMENT PRIMARY KEY,
  valve_status ENUM('ON','OFF') DEFAULT 'OFF',
  control_mode ENUM('manual','automatic') DEFAULT 'manual',
  moisture_threshold DECIMAL(5,2) DEFAULT 40.00,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT INTO system_status (valve_status, control_mode, moisture_threshold)
SELECT 'OFF', 'manual', 30.00
WHERE NOT EXISTS (SELECT 1 FROM system_status);
