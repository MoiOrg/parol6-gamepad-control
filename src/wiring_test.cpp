/*
 * PAROL6 — test cablage (1 moteur + butees)
 *
 * Usage:
 *  1. Change les variables dans "REGLAGES" ci-dessous.
 *  2. Flashe ce projet avec PlatformIO + ST-Link (24 V debranche pendant le flash).
 *  3. Ferme le moniteur PlatformIO, puis:  python sensor_gui.py
 *     (affichage brut + cases jaunes 3 s a chaque changement)
 *  Ou moniteur 115200: lignes "D L1=.. J1=.."
 *  4. Au boot, RIEN ne bouge (MOVE_ON_START = false). Les butees s'affichent.
 *  5. Tape les commandes puis Enter:
 *
 *       p / m        vitesse +400 / -400 sur l'axe choisi
 *       1..6         choisir l'axe (les autres continuent)
 *       s            stop tous les axes
 *       v800 / v-800 vitesse de l'axe choisi
 *       v2=-400      vitesse de J2 sans toucher aux autres
 *       a800,-400,0,0,0,0   les 6 vitesses a la fois (manette)
 *       k1=1 / k1=0  butee: bloque seulement le sens d'arrivee
 *       off / mou    coupe le couple (bras mou, tiens-le)
 *       on           remet le couple
 *
 *  Main sur l'E-stop. Plusieurs axes en meme temps OK.
 *  Remets le firmware PRINCIPAL quand tu as fini.
 */

#include <Arduino.h>
#include <SPI.h>
#include <TMCStepper.h>
#include <AccelStepper.h>

#define Serial SerialUSB

// Pins carte PAROL6
#define PUL1 PC6
#define PUL2 PA10
#define PUL3 PC0
#define PUL4 PC3
#define PUL5 PC9
#define PUL6 PC5
#define DIR1 PB15
#define DIR2 PA1
#define DIR3 PC1
#define DIR4 PA0
#define DIR5 PA8
#define DIR6 PB1
#define LIMIT1 PC12
#define LIMIT2 PB3
#define LIMIT3 PA15
#define LIMIT4 PD2
#define LIMIT5 PB4
#define LIMIT6 PC11
#define SELECT1 PC7
#define SELECT2 PA9
#define SELECT3 PC15
#define SELECT4 PC2
#define SELECT5 PC8
#define SELECT6 PC4
#define GLOBAL_ENABLE PA3
#define MOSI_PIN PA7
#define MISO_PIN PA6
#define SCK_PIN PA5
#define LED1 PB2
#define LED2 PB10
#define SUPPLY_ON_OFF PC10
#define SUPPLY_BUTTON_STATE PC14
#define ESTOP PB14
#define FLASH_SELECT PA4
#define INPUT1 PB6
#define INPUT2 PB5

#define R_SENSE 0.075f

// =============================================================================
// REGLAGES  —  change ici, reflash, ou utilise les commandes serie
// =============================================================================

// Axe a tester au demarrage: 1 = base, 2 = epaule, 3 = coude, 4, 5, 6 = poignet
static int TEST_JOINT = 1;

// Chaque appui p/m ajoute ce palier (steps/s). 1er p = 400, 2e = 800...
static int SPEED_STEP = 400;
static int SPEED_MAX = 8000;

// Courants RMS comme le firmware principal (sinon J2/J3 n'ont pas le couple).
static const int JOINT_CURRENT_MA[6] = {2100, 2100, 2000, 2000, 2000, 1000};

// false = au boot on lit seulement les butees.
static bool MOVE_ON_START = false;

// Par axe: si true, un front 0->1 de butee bloque CE sens-la, l'autre reste libre.
static bool stop_on_limit[6] = {false, false, false, false, false, false};

// Si true, E-stop a 0 coupe le mouvement (broche ESTOP = PB14).
static bool RESPECT_ESTOP = true;

// Ligne D pour le GUI: 40 ms, et tout de suite si une entree change
static const unsigned long PRINT_EVERY_MS = 40;

// =============================================================================
// Mapping firmware PRINCIPAL (motor_init.cpp) — ne pas confondre silk / axe
// J1 lit LIMIT6, J6 lit LIMIT1. C'est voulu.
// trigger: etat du pin quand la butee EST vue (0 = actif bas, 1 = actif haut)
// =============================================================================

static const int PUL_PIN[6]    = {PUL1, PUL6, PUL5, PUL4, PUL2, PUL3};
static const int DIR_PIN[6]    = {DIR1, DIR6, DIR5, DIR4, DIR2, DIR3};
static const int SEL_PIN[6]    = {SELECT1, SELECT6, SELECT5, SELECT4, SELECT2, SELECT3};
static const int LIMIT_PIN[6]  = {LIMIT6, LIMIT2, LIMIT3, LIMIT4, LIMIT5, LIMIT1};
static const char *LIMIT_NAME[6] = {"LIMIT6", "LIMIT2", "LIMIT3", "LIMIT4", "LIMIT5", "LIMIT1"};
static const int LIMIT_TRIGGER[6] = {0, 1, 1, 0, 1, 0};
static const int DIR_REVERSED[6]  = {1, 0, 1, 0, 0, 1};
static const char *JOINT_NAME[6] = {
    "J1 base",
    "J2 epaule",
    "J3 coude",
    "J4 poignet roll",
    "J5 poignet pitch",
    "J6 poignet yaw"};

TMC5160Stepper drivers[6] = {
    TMC5160Stepper(SELECT1, R_SENSE),
    TMC5160Stepper(SELECT6, R_SENSE),
    TMC5160Stepper(SELECT5, R_SENSE),
    TMC5160Stepper(SELECT4, R_SENSE),
    TMC5160Stepper(SELECT2, R_SENSE),
    TMC5160Stepper(SELECT3, R_SENSE),
};

AccelStepper steppers[6] = {
    AccelStepper(AccelStepper::DRIVER, PUL1, DIR1),
    AccelStepper(AccelStepper::DRIVER, PUL6, DIR6),
    AccelStepper(AccelStepper::DRIVER, PUL5, DIR5),
    AccelStepper(AccelStepper::DRIVER, PUL4, DIR4),
    AccelStepper(AccelStepper::DRIVER, PUL2, DIR2),
    AccelStepper(AccelStepper::DRIVER, PUL3, DIR3),
};

static int joint_index = 0;
static int run_speed[6] = {0, 0, 0, 0, 0, 0};
static int last_h[6] = {-1, -1, -1, -1, -1, -1};
static int lock_dir[6] = {0, 0, 0, 0, 0, 0};
static int dir_invert[6];
static String serial_line;
static bool estop_latched = false;
static bool motors_soft = false;

static int clamp_joint(int j)
{
  if (j < 1)
    j = 1;
  if (j > 6)
    j = 6;
  return j;
}

static bool limit_triggered(int i)
{
  return digitalRead(LIMIT_PIN[i]) == LIMIT_TRIGGER[i];
}

static int clamp_speed(int v)
{
  if (v > SPEED_MAX)
    v = SPEED_MAX;
  if (v < -SPEED_MAX)
    v = -SPEED_MAX;
  return v;
}

static void stop_all()
{
  for (int i = 0; i < 6; i++)
    run_speed[i] = 0;
}

static int apply_limit_dir(int i, int speed)
{
  int hit = limit_triggered(i) ? 1 : 0;
  if (!stop_on_limit[i])
  {
    lock_dir[i] = 0;
    last_h[i] = hit;
    return speed;
  }
  if (last_h[i] < 0)
  {
    last_h[i] = hit;
    return speed;
  }
  if (!hit)
  {
    lock_dir[i] = 0;
    last_h[i] = 0;
    return speed;
  }
  if (last_h[i] == 0 && speed != 0)
    lock_dir[i] = (speed > 0) ? 1 : -1;
  last_h[i] = 1;
  if (lock_dir[i] != 0 && ((speed > 0 && lock_dir[i] > 0) || (speed < 0 && lock_dir[i] < 0)))
    return 0;
  return speed;
}

static void print_help()
{
  Serial.println();
  Serial.println(F("  p/+ = +400   m/- = -400   s = stop tous   1..6 = axe"));
  Serial.println(F("  v800 / v2=-400 / a800,-400,0,0,0,0"));
  Serial.println(F("  k1=1 butee = bloque le sens d'arrivee seulement"));
  Serial.println(F("  off = mou (plus de couple, tiens le bras)   on = tenir"));
}

static const int SILK_LIMIT_PIN[6] = {LIMIT1, LIMIT2, LIMIT3, LIMIT4, LIMIT5, LIMIT6};

static void print_status()
{
  int silk[6];
  int jraw[6];
  for (int i = 0; i < 6; i++)
  {
    silk[i] = digitalRead(SILK_LIMIT_PIN[i]);
    jraw[i] = digitalRead(LIMIT_PIN[i]);
  }
  int estop = digitalRead(ESTOP);
  int in1 = digitalRead(INPUT1);
  int in2 = digitalRead(INPUT2);
  int btn = digitalRead(SUPPLY_BUTTON_STATE);

  // Ligne machine pour sensor_gui.py
  Serial.print(F("D"));
  for (int i = 0; i < 6; i++)
  {
    Serial.print(F(" L"));
    Serial.print(i + 1);
    Serial.print('=');
    Serial.print(silk[i]);
  }
  for (int i = 0; i < 6; i++)
  {
    Serial.print(F(" J"));
    Serial.print(i + 1);
    Serial.print('=');
    Serial.print(jraw[i]);
  }
  for (int i = 0; i < 6; i++)
  {
    Serial.print(F(" T"));
    Serial.print(i + 1);
    Serial.print('=');
    Serial.print(LIMIT_TRIGGER[i]);
  }
  for (int i = 0; i < 6; i++)
  {
    Serial.print(F(" H"));
    Serial.print(i + 1);
    Serial.print('=');
    Serial.print(jraw[i] == LIMIT_TRIGGER[i] ? 1 : 0);
  }
  for (int i = 0; i < 6; i++)
  {
    Serial.print(F(" K"));
    Serial.print(i + 1);
    Serial.print('=');
    Serial.print(stop_on_limit[i] ? 1 : 0);
  }
  Serial.print(F(" ESTOP="));
  Serial.print(estop);
  Serial.print(F(" IN1="));
  Serial.print(in1);
  Serial.print(F(" IN2="));
  Serial.print(in2);
  Serial.print(F(" BTN="));
  Serial.print(btn);
  Serial.print(F(" JSEL="));
  Serial.print(joint_index + 1);
  Serial.print(F(" SPD="));
  Serial.print(run_speed[joint_index]);
  Serial.print(F(" SOFT="));
  Serial.print(motors_soft ? 1 : 0);
  for (int i = 0; i < 6; i++)
  {
    Serial.print(F(" S"));
    Serial.print(i + 1);
    Serial.print('=');
    Serial.print(run_speed[i]);
  }
  Serial.println();
}

static void apply_motion()
{
  if (motors_soft)
  {
    for (int i = 0; i < 6; i++)
    {
      steppers[i].setMaxSpeed(SPEED_MAX + 100);
      steppers[i].setSpeed(0);
    }
    return;
  }

  bool estop = RESPECT_ESTOP && digitalRead(ESTOP) == 0;
  if (estop)
  {
    stop_all();
    if (!estop_latched)
    {
      Serial.println(F("STOP: E-stop"));
      estop_latched = true;
    }
  }
  else
    estop_latched = false;

  for (int i = 0; i < 6; i++)
  {
    int sp = estop ? 0 : apply_limit_dir(i, run_speed[i]);
    steppers[i].setMaxSpeed(SPEED_MAX + 100);
    steppers[i].setSpeed(sp);
  }
}

static void set_motors_soft(bool soft)
{
  stop_all();
  for (int i = 0; i < 6; i++)
    steppers[i].setSpeed(0);
  motors_soft = soft;
  // TMC5160 : GLOBAL_ENABLE HIGH coupe l'etage de puissance (bobines libres).
  digitalWrite(GLOBAL_ENABLE, soft ? HIGH : LOW);
  if (soft)
    Serial.println(F(">>> MOU  pas de couple. Tiens le bras (J2/J3 tombent). Tape on pour tenir."));
  else
    Serial.println(F(">>> TENU  couple remis. Position fausse si le bras a ete bouge a la main."));
  print_status();
}

static bool refuse_if_soft()
{
  if (!motors_soft)
    return false;
  Serial.println(F(">>> ignore: moteurs mous. Tape on pour tenir."));
  return true;
}

static void handle_line(String line)
{
  line.trim();
  line.toLowerCase();
  if (line.length() == 0)
    return;

  if (line == "help" || line == "h" || line == "?")
  {
    print_help();
    return;
  }
  if (line == "off" || line == "mou" || line == "soft")
  {
    set_motors_soft(true);
    return;
  }
  if (line == "on" || line == "tenu" || line == "hold")
  {
    set_motors_soft(false);
    return;
  }
  if (line == "stop" || line == "s")
  {
    stop_all();
    apply_motion();
    Serial.println(F(">>> STOP"));
    print_status();
    return;
  }
  if (line == "+" || line == "plus" || line == "p" || line == "go" || line == "g")
  {
    if (refuse_if_soft())
      return;
    run_speed[joint_index] = clamp_speed(run_speed[joint_index] + SPEED_STEP);
    apply_motion();
    Serial.print(F(">>> J"));
    Serial.print(joint_index + 1);
    Serial.print(F(" vitesse "));
    Serial.println(run_speed[joint_index]);
    print_status();
    return;
  }
  if (line == "-" || line == "minus" || line == "m")
  {
    if (refuse_if_soft())
      return;
    run_speed[joint_index] = clamp_speed(run_speed[joint_index] - SPEED_STEP);
    apply_motion();
    Serial.print(F(">>> J"));
    Serial.print(joint_index + 1);
    Serial.print(F(" vitesse "));
    Serial.println(run_speed[joint_index]);
    print_status();
    return;
  }
  if (line.charAt(0) == 'a')
  {
    if (refuse_if_soft())
      return;
    int idx = 0;
    int val = 0;
    int sign = 1;
    bool got = false;
    for (unsigned i = 1; i <= line.length() && idx < 6; i++)
    {
      char c = (i < line.length()) ? line.charAt(i) : ',';
      if (c == ',')
      {
        run_speed[idx] = got ? clamp_speed(sign * val) : 0;
        idx++;
        val = 0;
        sign = 1;
        got = false;
      }
      else if (c == '-')
      {
        sign = -1;
        got = true;
      }
      else if (c == '+')
      {
        sign = 1;
        got = true;
      }
      else if (c >= '0' && c <= '9')
      {
        val = val * 10 + (c - '0');
        got = true;
      }
    }
    apply_motion();
    return;
  }
  if (line.charAt(0) == 'v')
  {
    if (refuse_if_soft())
      return;
    int idx = joint_index;
    int start = 1;
    if (line.length() >= 4 && line.charAt(1) >= '1' && line.charAt(1) <= '6' && line.charAt(2) == '=')
    {
      idx = line.charAt(1) - '1';
      start = 3;
    }
    run_speed[idx] = clamp_speed(line.substring(start).toInt());
    apply_motion();
    return;
  }
  if (line.length() >= 2 && line.charAt(0) == 'k' && line.charAt(1) >= '1' && line.charAt(1) <= '6')
  {
    int i = line.charAt(1) - '1';
    if (line.length() >= 4 && line.charAt(2) == '=')
      stop_on_limit[i] = (line.charAt(3) != '0');
    else
      stop_on_limit[i] = !stop_on_limit[i];
    Serial.print(F("K"));
    Serial.print(i + 1);
    Serial.print(F("="));
    Serial.println(stop_on_limit[i] ? 1 : 0);
    print_status();
    return;
  }
  if (line == "lim" || line == "l")
  {
    bool any = false;
    for (int i = 0; i < 6; i++)
      if (stop_on_limit[i])
        any = true;
    for (int i = 0; i < 6; i++)
      stop_on_limit[i] = !any;
    Serial.println(F("stop-si-butee tous les axes bascules"));
    print_status();
    return;
  }
  if (line == "inv" || line == "i")
  {
    dir_invert[joint_index] = !dir_invert[joint_index];
    steppers[joint_index].setPinsInverted(dir_invert[joint_index]);
    Serial.print(F("J"));
    Serial.print(joint_index + 1);
    Serial.print(F(" direction_reversed = "));
    Serial.println(dir_invert[joint_index]);
    return;
  }
  if ((line.length() == 2 && line.charAt(0) == 'j' && line.charAt(1) >= '1' && line.charAt(1) <= '6') ||
      (line.length() == 1 && line.charAt(0) >= '1' && line.charAt(0) <= '6'))
  {
    TEST_JOINT = line.charAt(line.length() - 1) - '0';
    joint_index = TEST_JOINT - 1;
    Serial.print(F(">>> Axe J"));
    Serial.print(TEST_JOINT);
    Serial.print(' ');
    Serial.println(JOINT_NAME[joint_index]);
    print_status();
    return;
  }

  bool numeric = true;
  for (unsigned i = 0; i < line.length(); i++)
  {
    if (line.charAt(i) < '0' || line.charAt(i) > '9')
      numeric = false;
  }
  if (numeric)
  {
    if (refuse_if_soft())
      return;
    int v = line.toInt();
    if (v < 50)
      v = 50;
    if (v > SPEED_MAX)
      v = SPEED_MAX;
    int cur = run_speed[joint_index];
    run_speed[joint_index] = (cur < 0) ? -v : v;
    if (run_speed[joint_index] == 0)
      run_speed[joint_index] = v;
    apply_motion();
    Serial.print(F("Vitesse = "));
    Serial.println(run_speed[joint_index]);
    return;
  }

  Serial.println(F("Commande inconnue. Tape help"));
}

static void init_pins()
{
  pinMode(LIMIT1, INPUT);
  pinMode(LIMIT2, INPUT);
  pinMode(LIMIT3, INPUT);
  pinMode(LIMIT4, INPUT);
  pinMode(LIMIT5, INPUT);
  pinMode(LIMIT6, INPUT);
  pinMode(ESTOP, INPUT);
  pinMode(SUPPLY_BUTTON_STATE, INPUT);
  pinMode(INPUT1, INPUT);
  pinMode(INPUT2, INPUT);

  pinMode(PUL1, OUTPUT);
  pinMode(PUL2, OUTPUT);
  pinMode(PUL3, OUTPUT);
  pinMode(PUL4, OUTPUT);
  pinMode(PUL5, OUTPUT);
  pinMode(PUL6, OUTPUT);
  pinMode(DIR1, OUTPUT);
  pinMode(DIR2, OUTPUT);
  pinMode(DIR3, OUTPUT);
  pinMode(DIR4, OUTPUT);
  pinMode(DIR5, OUTPUT);
  pinMode(DIR6, OUTPUT);
  pinMode(SELECT1, OUTPUT);
  pinMode(SELECT2, OUTPUT);
  pinMode(SELECT3, OUTPUT);
  pinMode(SELECT4, OUTPUT);
  pinMode(SELECT5, OUTPUT);
  pinMode(SELECT6, OUTPUT);
  pinMode(GLOBAL_ENABLE, OUTPUT);
  pinMode(LED1, OUTPUT);
  pinMode(LED2, OUTPUT);
  pinMode(SUPPLY_ON_OFF, OUTPUT);
  pinMode(FLASH_SELECT, OUTPUT);
  digitalWrite(FLASH_SELECT, HIGH);

  digitalWrite(SELECT1, HIGH);
  digitalWrite(SELECT2, HIGH);
  digitalWrite(SELECT3, HIGH);
  digitalWrite(SELECT4, HIGH);
  digitalWrite(SELECT5, HIGH);
  digitalWrite(SELECT6, HIGH);
}

static void power_button()
{
  static uint32_t previous_milis = 0;
  static uint32_t turn_off_flag = 0;
  uint32_t current_milis = HAL_GetTick();
  if (current_milis - previous_milis < 100)
    return;
  previous_milis = current_milis;
  if (turn_off_flag == 1)
  {
    turn_off_flag = 0;
    digitalWrite(SUPPLY_ON_OFF, LOW);
  }
  if (digitalRead(SUPPLY_BUTTON_STATE) == HIGH)
  {
    turn_off_flag = 1;
    digitalWrite(SUPPLY_ON_OFF, HIGH);
  }
}

static void init_driver(int i)
{
  digitalWrite(SEL_PIN[i], HIGH);
  drivers[i].begin();
  drivers[i].rms_current((uint16_t)(JOINT_CURRENT_MA[i] * 0.85), 0.8);
  drivers[i].en_pwm_mode(0);
  drivers[i].toff(4);
  drivers[i].blank_time(24);
  drivers[i].pwm_autoscale(1);
  drivers[i].microsteps(32);

  int ok = drivers[i].test_connection();
  Serial.print(JOINT_NAME[i]);
  Serial.print(F("  driver SPI: "));
  Serial.println(ok == 0 ? "OK" : "ERREUR");

  dir_invert[i] = DIR_REVERSED[i];
  steppers[i].setMaxSpeed(SPEED_MAX + 100);
  steppers[i].setAcceleration(2000);
  steppers[i].setPinsInverted(dir_invert[i]);
  steppers[i].setSpeed(0);
}

void setup()
{
  TEST_JOINT = clamp_joint(TEST_JOINT);
  joint_index = TEST_JOINT - 1;

  init_pins();
  digitalWrite(SUPPLY_ON_OFF, HIGH);
  delay(200);
  digitalWrite(GLOBAL_ENABLE, HIGH);

  Serial.begin(115200);
  delay(1500);

  SPI.setMOSI(MOSI_PIN);
  SPI.setMISO(MISO_PIN);
  SPI.setSCLK(SCK_PIN);
  SPI.setClockDivider(SPI_CLOCK_DIV4);
  SPI.begin();
  delay(50);
  digitalWrite(GLOBAL_ENABLE, LOW);
  delay(100);

  Serial.println();
  Serial.println(F("=== PAROL6 wiring test ==="));
  print_help();
  Serial.println();

  for (int i = 0; i < 6; i++)
  {
    init_driver(i);
    delay(40);
  }

  Serial.print(F("Axe par defaut: J"));
  Serial.println(TEST_JOINT);
  if (MOVE_ON_START)
  {
    run_speed[joint_index] = SPEED_STEP;
    Serial.println(F("MOVE_ON_START=true -> rotation + (tape s pour arreter)"));
  }
  else
  {
    Serial.println(F("Moteurs en hold. Tape p  puis Entree  pour bouger."));
  }
  apply_motion();
  print_status();
}

void loop()
{
  power_button();

  apply_motion();

  for (int i = 0; i < 6; i++)
    steppers[i].runSpeed();

  while (Serial.available())
  {
    char c = Serial.read();
    if (c == '\r' || c == '\n')
    {
      if (serial_line.length() > 0)
        handle_line(serial_line);
      serial_line = "";
      continue;
    }
    if (serial_line.length() == 0 &&
        (c == '+' || c == '-' || c == 'p' || c == 'P' || c == 'm' || c == 'M' ||
         c == 's' || c == 'S' || (c >= '1' && c <= '6')))
    {
      handle_line(String(c));
      continue;
    }
    if (c >= 32 && c < 127)
    {
      serial_line += c;
      if (serial_line.length() > 80)
        serial_line = "";
    }
  }

  static uint32_t last_print = 0;
  static int last_silk[6] = {-1, -1, -1, -1, -1, -1};
  static int last_misc[4] = {-1, -1, -1, -1};
  uint32_t now = HAL_GetTick();
  bool changed = false;
  for (int i = 0; i < 6; i++)
  {
    int v = digitalRead(SILK_LIMIT_PIN[i]);
    if (v != last_silk[i])
    {
      last_silk[i] = v;
      changed = true;
    }
  }
  int misc[4] = {
      digitalRead(ESTOP),
      digitalRead(INPUT1),
      digitalRead(INPUT2),
      digitalRead(SUPPLY_BUTTON_STATE)};
  for (int i = 0; i < 4; i++)
  {
    if (misc[i] != last_misc[i])
    {
      last_misc[i] = misc[i];
      changed = true;
    }
  }
  if (changed || (now - last_print >= PRINT_EVERY_MS))
  {
    last_print = now;
    digitalWrite(LED1, !digitalRead(LED1));
    print_status();
  }
}
