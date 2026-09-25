// Password-protects this entire site with HTTP basic auth, at Netlify's edge.
//
// Netlify's own password protection is a Pro-plan feature. This is the free
// equivalent, and it runs in Netlify's Deno runtime before anything is served.
//
// NOTHING IS INSTALLED AND NOTHING IS FETCHED. The code below is the whole
// function, pasted in: no npm package, no node_modules, no package.json entry,
// and no import from an outside registry that could disappear. This replaced a
// one-line remote import from deno.land/x; that dependency is deliberately gone.
//
// THE PASSWORD IS NOT IN THIS FILE, AND MUST NOT BE. It is read at request time
// from an environment variable you set per site in the Netlify dashboard, under
// Site configuration > Environment variables:
//
//   name:   BASIC_AUTH_CREDENTIALS
//   format: username:password
//           several are space-separated -- e.g.  robbie:pick-a-password  client:another-one
//
// Avoid spaces and colons inside a password: a space starts the next
// username:password pair, and everything after the second colon is ignored on
// both sides. Letters, digits and punctuation like -_.!@#$% are all fine.
//
// Until that variable exists the function returns without doing anything and
// the site stays public, so this file on its own changes nothing. That also
// means a typo in the variable NAME fails open, silently: after setting it,
// load the site in a private window and check you actually get a prompt.
//
// What this is and is not: a front-door lock that keeps the public and search
// engines out. The credentials travel over HTTPS, but they are compared as
// plain text against a plain-text environment variable, so treat it as "not for
// strangers" rather than as protection for anything genuinely sensitive.
//
// ---------------------------------------------------------------------------
// Everything below this line is v1.0.0 of
// acestojanoski/netlify-basic-auth-edge-function, copied verbatim
// (sha256 930e0fa89cc15f65d5d8e92200ed060822531b640f0e9e4029ba0989bf9ade14 --
// the deno.land/x and GitHub copies were byte-identical to each other and to
// this). The only addition is the `export default handler` line at the end,
// which replaces the re-export the old remote import used to do.
//
// Reproduced under its MIT licence, which requires this notice to travel with
// the code:
//
//   MIT License
//
//   Copyright (c) Aleksandar Stojanoski
//
//   Permission is hereby granted, free of charge, to any person obtaining a
//   copy of this software and associated documentation files (the "Software"),
//   to deal in the Software without restriction, including without limitation
//   the rights to use, copy, modify, merge, publish, distribute, sublicense,
//   and/or sell copies of the Software, and to permit persons to whom the
//   Software is furnished to do so, subject to the following conditions:
//
//   The above copyright notice and this permission notice shall be included in
//   all copies or substantial portions of the Software.
//
//   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
//   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
//   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
//   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
//   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
//   FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
//   DEALINGS IN THE SOFTWARE.
// ---------------------------------------------------------------------------

function unauthorized() {
  return new Response('Unauthorized', {
    status: 401,
    headers: {
      'www-authenticate': 'basic',
    },
  })
}

export function handler(request: Request) {
  try {
    const credentialsConfig = Deno.env.get('BASIC_AUTH_CREDENTIALS')

    // Continue without authentication if no config is found
    if (!credentialsConfig) {
      return undefined
    }

    const authorization = request.headers.get('authorization')

    if (!authorization) {
      return unauthorized()
    }

    const base64Credentials = authorization.split('Basic ')[1]

    if (!base64Credentials) {
      return unauthorized()
    }

    const [username, password] = atob(base64Credentials).split(':')

    const allowedCombinations = credentialsConfig
      .split(' ')
      .map((credentials) => credentials.split(':'))

    const isAuthorized = allowedCombinations.some(
      ([allowedUsername, allowedPassword]) =>
        allowedUsername === username && allowedPassword === password,
    )

    if (!isAuthorized) {
      return unauthorized()
    }
  } catch (error) {
    console.error('[netlify-basic-auth-edge-function] error', error)

    return new Response('Bad Gateway', {
      status: 502,
    })
  }
}

export default handler
