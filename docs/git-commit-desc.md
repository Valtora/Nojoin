v0.5.4

fix(system): ensure demo data is seeded during setup

Previously, the setup wizard created the admin user but failed to trigger the demo data seeding process. This resulted in a missing 'Welcome to Nojoin' recording and a broken tour experience for new installations.

This commit adds the missing call to 'seed_demo_data' in the 'setup_system' endpoint.
