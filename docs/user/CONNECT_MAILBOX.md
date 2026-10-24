# Connect your mailbox (optional)

You only need this if you want Lead Generator to **send emails from your own mailbox** and to read replies, bounces and unsubscribes. You do **not** need it to:

- find people and write messages,
- export a CSV file,
- send through lemlist or Instantly (they do their own sending).

Skip this page unless you want the sending. Read [Delivery](DELIVERY.md) and [Compliance](COMPLIANCE.md) before you send any email.

## Where

In the dashboard (`leadgen dashboard --open`), open **Connect your mailbox**. It is linked from the Home page, from Settings and from the sending part of a campaign. The page has two sections: **Gmail or Google Workspace**, and **Outlook or another mailbox**.

## Gmail or Google Workspace: three steps

Google does not let programs use your normal password. It asks for a special one-time password called an **app password**. It takes about two minutes.

1. **Turn on 2-Step Verification.** The page has a button that opens Google's 2-Step Verification page in a new tab. If it is already on, go to step 2.
2. **Make an app password.** Use the second button to open Google's app passwords page. Type `Lead Generator` as the name and press **Create**. Google shows 16 letters in four groups. Copy them.
3. **Paste your email address and the 16 letters** on the Lead Generator page and save. Spaces are removed for you and the mail servers are filled in for you. Lead Generator logs in to check, and only keeps the details if that works.

The password is kept on this computer only and is never shown again. Only its last 4 characters are ever displayed.

Press **Test it again (sends nothing)** at any time. It logs in for sending and for reading, and sends no email.

### If it does not work

- **The password was not accepted:** use the 16-letter app password, not your normal password. Make a new one and try again.
- **No place to make an app password:** some work or school accounts, and accounts with Google's Advanced Protection, do not allow app passwords. Ask your administrator, or use another mailbox in the other section.
- **It cannot read the inbox:** reading replies needs access to your inbox. On some accounts your administrator has to allow it.

## Outlook, Microsoft 365 or another mailbox

Use the second section and fill in the details your email provider gives you (sending server and port, reading server, address and password).

Microsoft 365 may need your IT administrator to switch on "SMTP AUTH" for your mailbox, and some accounts refuse passwords altogether. Lead Generator cannot use Microsoft's newer sign-in method yet. If that is your case, send through lemlist or Instantly with a CSV file instead.

## Terminal alternative

`leadgen mailbox set --preset gmail` asks for your address and password (hidden while you type), and `leadgen mailbox test` checks the login.

## Taking the mailbox away

There is no button for this yet. Delete the lines that start with `SMTP_` and `IMAP_` from the `secrets.env` file in your LeadGenerator folder (`leadgen paths` shows where it is).

## Safety

Never type your mailbox password into the chat. Sending always starts as a dry run and needs your clear yes. It starts at 20 emails a day and only emails people with a **verified** address. See [Delivery](DELIVERY.md).
