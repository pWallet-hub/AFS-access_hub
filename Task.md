# AFS Command Center
## Product Requirements, Technical Architecture and Implementation Specification

## 1. Project Overview

AFS Command Center is a centralized application access and Single Sign-On (SSO) platform for the AFS ecosystem. Its purpose is to provide employees with one central place to access the applications they are authorized to use, while allowing administrators to manage users, application assignments and access permissions from a unified interface.

AFS has multiple applications, each with its own authentication system, users, roles and permissions. The objective is to introduce centralized SSO without unnecessarily replacing or disrupting the authentication systems already in use.

The platform will use Keycloak as the intended central identity provider, subject to the developer confirming the final architecture. Existing applications will retain their original login methods while gaining an additional AFS SSO login option.

The platform must be extensible so additional applications can be integrated in the future without redesigning the entire system.

### Core objectives

- Provide centralized authentication through SSO.
- Give employees one place to discover and launch their authorized applications.
- Allow employees to access applications directly through their URLs.
- Allow administrators to manage users, application assignments and application-specific roles centrally.
- Preserve existing application authentication and user accounts wherever possible.
- Enforce permissions independently within each application.
- Provide a Docker-based environment that can be started without manually installing or configuring the application's infrastructure services.
- Make adding new applications straightforward, secure and well documented.

## 2. Developer Freedom and Technical Decision-Making

The developer has the freedom and responsibility to choose the best technology stack, architecture, libraries, project structure, database design and implementation patterns for this project.

The technologies and architectural patterns described in this specification are recommendations and requirements at the capability level, not rigid implementation constraints.

The developer should evaluate the existing repositories and choose a stack that is secure, maintainable, suitable for the team's skills, easy to run using Docker, and appropriate for integrating the different applications.

The developer may choose the frontend framework, backend framework, programming language, database access layer, testing tools, monorepo or multi-repository structure, and shared integration strategy.

Keycloak is the intended identity provider. If the developer believes an alternative is materially better, that decision should be documented with its benefits, drawbacks, migration implications and operational requirements before implementation.

The developer should prioritize simplicity and maintainability over unnecessary complexity. A modular application is preferable to introducing multiple new microservices unless there is a clear technical or operational reason.

Regardless of the chosen stack, the implementation must satisfy the functional requirements, user stories, security requirements and acceptance criteria in this specification.

## 3. Applications in Scope

The initial integration scope consists of five repositories.

### 3.1 Biocap Rwanda

Repository: https://github.com/felixaveiro/Biocap-rwanda

Requirements:
- Inspect its existing authentication, sessions, user records and permissions.
- Introduce an additional AFS SSO login option.
- Preserve its existing login functionality.
- Link authenticated SSO identities to existing application users safely.
- Ensure the backend continues enforcing its own authorization rules.

### 3.2 Biocap Dashboard Backend

Repository: https://github.com/pWallet-hub/Biocap-dashboard-backend

Requirements:
- Inspect API authentication, token validation, user management and authorization middleware.
- Establish a trusted authentication path for Keycloak identities.
- Map central application assignments to the dashboard's actual roles and permissions.
- Protect APIs against unauthorized direct requests.
- Preserve existing application behavior.

### 3.3 Procurement BN

Repository: https://github.com/pWallet-hub/Procurrement-bn

Requirements:
- Inspect existing authentication, user records, role definitions and protected procurement operations.
- Introduce AFS SSO while retaining the original login.
- Map centrally assigned roles to the roles recognized by the application.
- Ensure approval, modification and administrative operations remain protected by the appropriate permissions.

### 3.4 Procurement FN

Repository: https://github.com/pWallet-hub/Procurrement-fn

Requirements:
- Audit its authentication and authorization implementation.
- Compare its architecture with Procurement BN.
- Reuse compatible integration components where practical.
- Maintain independent application permissions and role mappings.
- Test authorization boundaries and protected financial operations.

### 3.5 ImageUnit

Repository: https://github.com/IshKevin/ImageUnit

Requirements:
- Inspect authentication, sessions, APIs and protected resources.
- Introduce the additional AFS SSO login option.
- Preserve the existing login system.
- Map central identities to existing ImageUnit accounts.
- Protect uploads, images, administrative features and other restricted resources through backend authorization.

The developer must inspect the actual source code before deciding how each integration should be implemented. The repository names do not establish which frameworks, databases or authentication protocols are currently used.

## 4. Product Architecture

The platform should have the following logical components. They do not necessarily need to be separate deployable services.

### 4.1 Command Center Web Application

The web interface will provide two primary experiences.

**Employee portal**
- Display applications assigned to the signed-in employee.
- Provide application descriptions and launch links.
- Show useful information when an application is disabled or unavailable.
- Provide account and session-related navigation where appropriate.

**Administrative console**
- Manage employee accounts.
- Register and configure applications.
- Assign and revoke application access.
- Assign application-specific roles.
- View access requests if that feature is implemented.
- Review audit records and integration status.
- Monitor provisioning and synchronization failures.

### 4.2 Central Identity Provider

Keycloak is the proposed identity provider for central authentication and SSO.

It will handle central login, identity verification, SSO sessions and issuing trusted identity tokens through supported standards such as OpenID Connect.

The implementation should use maintained authentication libraries and standard protocols instead of creating a custom authentication protocol.

### 4.3 Command Center Backend

The backend will manage the platform's business logic, including:

- Employee and account management.
- Application registration and configuration.
- Application access assignments.
- Application-specific role mappings.
- Administrative authorization.
- Identity links to existing application accounts.
- Integration and provisioning status.
- Audit logging.
- Any approved access-request workflow.

The backend must independently enforce administrative permissions. Frontend controls must never be the only protection for sensitive operations.

### 4.4 Database and Persistent Storage

The developer may choose the database and data access technology. PostgreSQL is a reasonable default.

The platform needs persistent storage for application metadata, assignments, identity links, administrative records and audit events. Keycloak's own persistent data must also survive container restarts and recreation.

Business data belonging to the connected applications should remain in those applications' existing databases.

### 4.5 Application Integration Layer

The system should use a consistent integration contract wherever practical.

That contract should define:
- Authentication initiation and callback handling.
- Trusted identity validation.
- Account linking and provisioning rules.
- Application session creation.
- Application access enforcement.
- Application-specific role mapping.
- Logout behavior.
- Account disablement and access revocation.
- Integration health and error reporting.

The developer may implement shared libraries, middleware or separate adapters depending on the technologies used by the existing applications. Different stacks may require different adapters.

## 5. Authentication and Access Behavior

The system must support both central application navigation and direct application access.

### 5.1 Central portal access

1. An employee opens AFS Command Center.
2. The employee signs in through Keycloak.
3. Command Center determines the applications assigned to that employee.
4. The employee sees only enabled applications they are authorized to access.
5. When the employee opens an application, the destination application validates the authentication result and enforces its permissions.

### 5.2 Direct URL access

Employees must also be able to open an application directly using its URL without visiting Command Center first.

For an integrated application:

1. If the user already has a valid application session, the application opens normally.
2. If the user has a valid Keycloak SSO session but no application session, the application redirects to Keycloak and can authenticate the user without requesting credentials again.
3. If the user has no valid SSO session, Keycloak presents the login page.
4. After successful authentication, the application returns the user to the originally requested page.
5. The application checks whether the user is assigned to it and whether the requested operation is permitted.
6. If access is not authorized, the application denies access.

Redirect handling must validate destinations to prevent open redirects.

### 5.3 Preserve existing authentication

Every existing application must retain its original authentication method during the integration unless an explicit, separately approved migration decision is made.

The additional SSO path must not break existing users, passwords, sessions, data or application-specific permissions.

The developer must establish a clear distinction between local authentication and trusted SSO authentication and prevent account-linking vulnerabilities.

### 5.4 Identity and account linking

The central identity and each application's existing user account must be linked through an explicit, stable identity mapping.

The implementation must not rely solely on matching email addresses to establish identity ownership.

When an employee signs in through SSO, the destination application must:
- Validate the trusted authentication result.
- Resolve the central identity to the correct existing account.
- Apply the application's provisioning policy.
- Confirm application access.
- Resolve the appropriate application-specific roles.
- Establish its own session or trusted API authentication context.

Where safe automatic provisioning is not possible, the integration should provide a controlled account-linking or administrator-assisted process.

## 6. Permission Management

The system must distinguish between three permission layers.

### Platform permissions

These control what someone can do inside Command Center, such as manage applications, manage employee accounts or administer access assignments.

Example platform roles might include `super_admin`, `access_admin` and `employee`. The developer may choose a different model if it satisfies the requirements.

### Application assignments

These determine which applications an employee is permitted to access.

For example, one employee may have access to Biocap Dashboard and ImageUnit but not Procurement BN or Procurement FN.

### Application-specific permissions

These determine what the employee can do inside a particular application.

For example, an employee could be a viewer in Biocap Dashboard, an approver in Procurement BN and have no access to ImageUnit.

Application roles must be scoped to their respective applications. A role called `admin` in one application must not automatically confer administrator privileges in another.

Command Center should be the central management point for application assignments. Existing applications should continue enforcing their own business permissions, with central role assignments mapped to those permissions where the integration supports it.

Removing an application assignment must prevent new unauthorized access. The implementation must also document and handle existing sessions and issued tokens according to a defined revocation policy.

## 7. User Stories and Acceptance Criteria

### Epic A: SSO and authentication

**SSO-01 — Central login**

As an AFS employee, I want to sign in through Keycloak so that I can access the applications assigned to me using a central identity.

Acceptance criteria:
- Users can authenticate through the configured identity provider.
- Authentication responses and tokens are validated correctly.
- Unassigned employees cannot gain application access simply by signing in.
- Failed authentication produces a clear, safe error message.

**SSO-02 — Preserve original login**

As an existing application user, I want to continue using the application's original login so that the SSO rollout does not disrupt my work.

Acceptance criteria:
- The original login remains functional.
- Existing accounts and business data are preserved.
- Existing application permissions continue to be enforced.
- SSO identity linking is safe and does not silently create duplicate accounts.

**SSO-03 — Cross-application SSO**

As an authenticated employee, I want to open another integrated application without entering my credentials again.

Acceptance criteria:
- The second application can reuse the active Keycloak SSO session.
- The destination application establishes its own appropriate session.
- The destination application still checks authorization.
- Logout and session-expiry behavior is documented and tested.

**SSO-04 — Direct URL access**

As an employee, I want to open an application directly through its URL so that I do not need to visit Command Center first.

Acceptance criteria:
- Unauthenticated users are redirected through the appropriate SSO flow.
- Successful authentication returns users to the intended destination.
- Unauthorized users are denied access.
- Redirect validation prevents unsafe destinations.

### Epic B: Employee and access management

**IAM-01 — Employee administration**

As an authorized administrator, I want to create, update, disable and reactivate employee accounts so that I can control access to AFS systems.

Acceptance criteria:
- Only authorized administrators can manage employee accounts.
- Each employee has a stable central identity.
- Account changes are recorded in the audit trail.
- Disabling an account prevents new authentication and triggers the documented revocation process.

**IAM-02 — Application assignments**

As an administrator, I want to assign specific applications to individual employees so that each employee can access only the applications required for their work.

Acceptance criteria:
- Administrators can grant and revoke access independently per application.
- The portal displays current assignments.
- Direct URLs and protected APIs are still enforced by the destination application.
- Changes take effect according to the documented session and revocation policy.

**IAM-03 — Application-specific roles**

As an administrator, I want to assign different roles in different applications so that each employee receives the correct privileges.

Acceptance criteria:
- Role definitions are scoped to their applications.
- Existing roles are mapped explicitly.
- Unsupported role assignments are rejected or flagged.
- Application backends enforce the effective permissions.

**IAM-04 — Integration monitoring**

As an administrator, I want to see account synchronization and integration failures so that I can resolve access problems.

Acceptance criteria:
- Failures are logged with useful diagnostic information.
- Retry operations are safe and do not duplicate accounts or assignments.
- The interface shows integration status and last successful synchronization where applicable.

### Epic C: Application registry and launcher

**APP-01 — Register an application**

As an administrator, I want to register and configure an application so that employees can access it through Command Center.

Acceptance criteria:
- Each application has a name, description, URL, status and integration configuration.
- OIDC settings are configurable.
- Secrets are protected and never exposed to browser code.
- Applications can be enabled or disabled without modifying frontend source code for ordinary configuration changes.

**APP-02 — Launch authorized applications**

As an employee, I want to see my assigned applications in one place so that I can easily find the systems I need.

Acceptance criteria:
- Only authorized, enabled applications are shown.
- Launch links use the configured application URLs.
- Application access is rechecked by the destination application.
- Unavailable applications display a useful status.

**APP-03 — Manage role mappings**

As an administrator, I want to manage application-specific role mappings so that central assignments correspond to actual application privileges.

Acceptance criteria:
- Role mappings are associated with individual applications.
- Only authorized administrators can modify mappings.
- Changes are validated and auditable.

### Epic D: Security and auditing

**SEC-01 — Audit access changes**

As a security administrator, I want privileged actions recorded so that I can investigate who granted, changed or revoked access.

Acceptance criteria:
- Logs capture the actor, action, target, application, timestamp and outcome.
- Logs are protected against unauthorized modification.
- Passwords, tokens and session cookies are never written to logs.

**SEC-02 — Revoke access safely**

As an administrator, I want access removed when an employee leaves or changes responsibilities.

Acceptance criteria:
- Disabled identities cannot start new authenticated sessions.
- Removed assignments prevent new unauthorized access.
- Existing sessions and tokens are addressed by the defined revocation strategy.
- Failed revocation operations are visible.

**SEC-03 — Automated integration tests**

As a developer, I want automated tests covering authentication and authorization so that changes do not break existing login behavior or create access vulnerabilities.

Acceptance criteria:
- Tests cover valid login, invalid tokens, missing assignments, account linking and role enforcement.
- Direct URL access and callback handling are tested.
- Original login functionality is covered by regression tests.
- Tests include application API authorization, not only frontend navigation.

## 8. Docker-Based Development and Deployment

The Command Center platform must be containerized and provide a complete, repeatable local development environment.

The repository should contain all configuration and scripts needed to run Command Center's own services without requiring developers to manually install application dependencies or separately configure its database and identity provider.

The developer may choose the container orchestration and packaging approach, but Docker Compose is the recommended default for local development.

### Required capabilities

The local environment must include:
- The web frontend.
- The backend API.
- Keycloak or the approved identity provider.
- A persistent database.
- Any required reverse proxy or supporting service.
- Database migrations and initialization scripts.
- Identity-provider realm/client initialization.
- Health checks and useful startup diagnostics.
- Environment-variable templates.
- Automated tests and documentation.

The five existing applications are not automatically part of this deployment. They remain independently deployed systems unless the developer explicitly implements an optional integration-test environment that runs compatible versions of them.

### Expected startup workflow

The documented local workflow should be approximately:

```bash
git clone <repository-url>
cd afs-command-center
cp .env.example .env
docker compose up --build -d
docker compose ps
```

The repository must explain how to configure the environment variables and access the local portal.

Docker Engine and the Docker Compose plugin are host prerequisites. All other application runtime dependencies should be provided by the container environment.

### Startup and persistence requirements

- Database readiness must be checked before dependent services start.
- Migrations must run safely and report errors clearly.
- Keycloak must be configured reproducibly.
- The frontend and backend must not start in a falsely healthy state when required dependencies are unavailable.
- Persistent data must survive container recreation.
- Database backup and restoration procedures must be documented.
- The repository must provide commands for starting, stopping, inspecting logs and running tests.
- Destructive commands that delete persistent volumes must be clearly documented.

Production deployment must use secure secret injection, HTTPS, restricted administrative endpoints, persistent storage and an appropriate backup strategy.

## 9. Configuration and Application Onboarding

Application configuration should be managed through the Command Center backend and administration interface wherever practical.

Application records should support:
- Name and description.
- Application launch URL.
- Enabled status.
- Authentication and integration type.
- OIDC client identifier and related non-secret configuration.
- Secure server-side secret references.
- Application-specific role mappings.
- Optional health-check configuration.
- Integration status and error information.

The initial registry should contain all five applications listed in Section 3, using configurable URLs. The developer must verify the actual frontend URLs and distinguish frontend applications from backend-only repositories.

Adding a new application should ordinarily involve registering its metadata, configuring its identity integration, mapping roles and validating access. New integration code may be required when an application uses a different framework or authentication model, but adding an ordinary application must not require hardcoding it into the portal UI.

## 10. Security Requirements

Security is a core requirement rather than a final development phase.

The implementation must:
- Use maintained OIDC/OAuth libraries and established security standards.
- Validate token signatures, issuer, audience, expiration and other relevant claims.
- Use secure session management and appropriate cookie attributes.
- Prevent open redirects and unsafe callback handling.
- Avoid account linking based solely on unverified email matches.
- Keep production credentials, client secrets and signing keys out of source code and images.
- Restrict privileged administration and provide a secure initial administrator setup.
- Protect all sensitive backend operations with authorization checks.
- Keep databases and identity-provider administration interfaces private in production.
- Use HTTPS for production authentication flows.
- Log privileged changes without logging sensitive credentials or tokens.
- Define account disablement, session invalidation and access-revocation behavior.
- Document backups, recovery and dependency/image update procedures.

The platform must not assume that hiding an application in the frontend is sufficient to secure it.

## 11. Testing Requirements

Testing must cover the platform and its integrations.

### Unit tests
- Access assignment and revocation logic.
- Application-role mapping.
- Administrative authorization.
- Identity-link resolution.
- Input validation and application configuration.

### Integration tests
- Backend/database interaction.
- Keycloak configuration and authentication.
- Token validation and account linking.
- Provisioning and synchronization failures.
- Access revocation and audit logging.

### End-to-end tests
- Employee signs in to Command Center.
- Employee sees only assigned applications.
- Employee opens an integrated application.
- Employee accesses the same application directly by URL.
- Employee opens a second application without re-entering credentials when an SSO session is valid.
- Unauthorized users are denied access to protected pages and APIs.
- Existing local login continues to work.
- Removing access prevents new unauthorized access.
- Application-specific permissions are enforced.
- The Docker environment starts and remains functional across container restarts.

The developer should provide repeatable test commands and report any tests that require external application environments or credentials.

## 12. Implementation Phases

### Phase 1 — Repository audit and design
Inspect all five repositories, document their authentication and authorization systems, identify existing roles and data models, and prepare an integration matrix.

Deliverable: a verified technical assessment and proposed pilot application.

### Phase 2 — Docker foundation and identity
Create the project structure, container environment, database, Keycloak configuration, initialization scripts and secure administrator bootstrap.

Deliverable: a working local environment with central login.

### Phase 3 — Core Command Center
Implement employee management, the application registry, access assignments, role mappings, the application launcher and audit logging.

Deliverable: a functional management portal.

### Phase 4 — Pilot integration
Integrate the selected application end to end, preserving its original authentication system.

Deliverable: a verified example of direct URL access, SSO, account linking and backend authorization.

### Phase 5 — Remaining integrations
Integrate the other four applications using the patterns established during the pilot, adapting to each application's architecture.

Deliverable: individually tested integrations and documented exceptions.

### Phase 6 — Hardening and release
Complete security testing, account-revocation handling, operational documentation, automated tests, backup/restore validation and deployment preparation.

Deliverable: a tested release with repeatable setup and maintenance procedures.

## 13. Definition of Done

The initial release is considered complete when:

1. The developer can start the Command Center environment from a clean checkout using the documented Docker workflow.
2. The web application, backend, database and identity provider initialize correctly.
3. An administrator can manage employees and application assignments.
4. Employees can sign in centrally and see their authorized applications.
5. Direct application URLs can initiate SSO and return users to their intended destination.
6. Existing application authentication remains functional.
7. Different employees can have different roles in different applications.
8. Destination backends enforce application access and internal permissions.
9. Account linking, disablement and revocation follow documented, tested policies.
10. New applications can be registered without changing portal source code for ordinary configuration.
11. Automated tests cover core authentication, authorization and integration behavior.
12. The repository contains complete setup, troubleshooting, security and deployment documentation.

An application must not be described as SSO-integrated until its authentication, identity resolution and authorization behavior have been verified end to end.

## 14. Technical References

The developer should use the official documentation for the selected implementation.

- Keycloak: https://www.keycloak.org/documentation
- Keycloak application integration: https://www.keycloak.org/securing-apps/overview
- OpenID Connect Core: https://openid.net/specs/openid-connect-core-1_0.html
- OAuth 2.0 Security Best Current Practice: https://www.rfc-editor.org/rfc/rfc9700.html
- Docker Compose: https://docs.docker.com/compose/

## Final Direction

Build AFS Command Center as a maintainable, Docker-based centralized SSO and application access management platform. Preserve existing application authentication, add a secure SSO option, support direct URL access, centralize application assignments and administrative controls, and retain application-specific authorization.

The developer is empowered to choose the best stack, architecture and project structure. That freedom should be exercised after inspecting the existing repositories and should result in a simpler, secure and maintainable solution—not unnecessary complexity.

The first deliverable should be a verified audit of all five repositories, followed by a documented architecture decision and a working end-to-end pilot integration. The rest of the implementation should build on that tested foundation.