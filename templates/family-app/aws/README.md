These are IAM policies for sending the site's email through Amazon SES. Replace `ACCOUNT_ID`, the region, `example.com` and the From address with your own before using them.

`ses-send-policy.json` is for an IAM user (for example `family-mailer`) whose access key goes in `/etc/family.env` on the server. It can only call `ses:SendEmail`, and only with the site's From address, so a leaked key cannot send as anyone else or do anything other than send mail. Create the user with no console access, attach the policy, and create an access key of the kind "Application running outside AWS".

A new SES account starts in the sandbox, where it sends only to addresses that are set up as SES identities. While it is there, add each test recipient as an identity and list its ARN in the policy's `Resource`; request production access in the SES console before inviting relatives, and remove the test identities afterwards.

You also need the domain set up as an SES identity, with Easy DKIM and, ideally, a custom MAIL FROM domain and a DMARC record; the SES console walks through these. An AI agent can do this setup only with an IAM user whose permissions you have limited to those identities; never give an agent a key that can change IAM.
