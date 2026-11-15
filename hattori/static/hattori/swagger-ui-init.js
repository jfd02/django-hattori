/**JS file for handling the SwaggerUIBundle and avoid inline script */
const csrfSettings = document.querySelector("body").dataset
const configJson = document.getElementById("swagger-settings").textContent;
const configObject = JSON.parse(configJson);

configObject.dom_id = "#swagger-ui";
configObject.presets = [
    SwaggerUIBundle.presets.apis,
    SwaggerUIBundle.SwaggerUIStandalonePreset
];

if (csrfSettings.apiCsrf && csrfSettings.csrfToken) {
    configObject.requestInterceptor = (req) => {
        req.headers['X-CSRFToken'] = csrfSettings.csrfToken
        return req;
    };
};

const ui = SwaggerUIBundle(configObject);
